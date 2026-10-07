"""Publicadores: la única parte que habla con X.

``TwikitPublisher`` usa twifork (fork mantenido de twikit), que publica a
través de la API interna de x.com con las cookies de una sesión real del
navegador. ``DryRunPublisher`` no publica nada y sirve para probar.

Las excepciones de la librería se traducen a las de este módulo para que el
resto del bot no dependa de twikit y se pueda probar sin red.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class PublisherError(Exception):
    """Base de los errores de publicación."""


class AuthError(PublisherError):
    """Cookies caducadas, cuenta bloqueada o suspendida. Requiere intervención.

    ``reason`` indica qué hay que hacer para arreglarlo:
    ``locked`` (resolver la verificación en x.com), ``suspended`` (revisar la
    cuenta) o ``cookies`` (volver a guardar las cookies).
    """

    LOCKED = "locked"
    SUSPENDED = "suspended"
    COOKIES = "cookies"

    def __init__(self, message: str, reason: str = COOKIES) -> None:
        super().__init__(message)
        self.reason = reason


class RateLimited(PublisherError):
    """X ha limitado la cuenta. Hay que esperar; no reintentar en esta ejecución."""

    def __init__(self, message: str, reset_at: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class Duplicate(PublisherError):
    """X rechaza el texto por duplicado."""


class TransientError(PublisherError):
    """Error de red o 5xx: se puede reintentar."""


class Publisher(Protocol):
    async def check_auth(self) -> str: ...
    async def post(self, text: str, reply_to: str | None = None) -> str: ...
    def persist_cookies(self) -> None: ...
    async def close(self) -> None: ...


class DryRunPublisher:
    """No publica: escribe en el log lo que se habría publicado."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.posted: list[tuple[str, str | None]] = []

    async def check_auth(self) -> str:
        return "dry-run"

    async def post(self, text: str, reply_to: str | None = None) -> str:
        tweet_id = f"dry-{next(self._ids)}"
        self.posted.append((text, reply_to))
        log.info("[dry-run] %s%s", f"(respuesta a {reply_to}) " if reply_to else "", text)
        return tweet_id

    def persist_cookies(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, (OSError, TimeoutError)):
        return True
    module = type(exc).__module__ or ""
    return module.startswith(("httpx", "httpcore", "curl_cffi", "anyio", "h11", "h2"))


class TwikitPublisher:
    """Publica con twifork usando un fichero de cookies (``auth_token`` y ``ct0``)."""

    def __init__(
        self,
        cookies_path: Path,
        *,
        language: str = "es-ES",
        impersonate: str | None = "chrome124",
        proxy: str | None = None,
        account: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        try:
            import twikit
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise PublisherError(
                "No está instalado twifork: pip install 'twifork[impersonate]'"
            ) from exc

        self._tw = twikit
        self._cookies_path = cookies_path
        self._account = account
        kwargs = {"proxy": proxy}
        if user_agent:
            kwargs["user_agent"] = user_agent
        if impersonate:
            kwargs["impersonate"] = impersonate
        self._client = twikit.Client(language, **kwargs)
        try:
            self._client.load_cookies(str(cookies_path))
        except FileNotFoundError as exc:
            raise AuthError(f"No existe el fichero de cookies: {cookies_path}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthError(f"Fichero de cookies inválido ({cookies_path}): {exc}") from exc

    # -- traducción de errores -------------------------------------------------
    def _translate(self, exc: Exception) -> PublisherError:
        tw = self._tw
        if isinstance(exc, PublisherError):
            return exc
        auth_types = tuple(
            t
            for t in (
                getattr(tw, "InvalidSession", None),
                getattr(tw, "Unauthorized", None),
                getattr(tw, "AccountLocked", None),
                getattr(tw, "AccountSuspended", None),
            )
            if t is not None
        )
        if isinstance(exc, auth_types):
            return self._translate_auth(exc)
        if isinstance(exc, tw.TooManyRequests):
            return RateLimited(f"429: {exc}", getattr(exc, "rate_limit_reset", None))
        if isinstance(exc, tw.DuplicateTweet):
            return Duplicate(str(exc))
        if isinstance(exc, tw.CouldNotTweet):
            msg = str(exc)
            # p. ej. "You've hit the daily limit. Subscribe to Premium... (501)"
            if "limit" in msg.lower():
                return RateLimited(msg)
            return PublisherError(f"X rechazó el tuit: {msg}")
        transient = tuple(
            t
            for t in (
                getattr(tw, "ServerError", None),
                getattr(tw, "RequestTimeout", None),
                getattr(tw, "NotFound", None),  # 404 esporádicos del handshake
                getattr(tw, "ClientTransactionError", None),
            )
            if t is not None
        )
        if isinstance(exc, transient) or _is_network_error(exc):
            return TransientError(f"{type(exc).__name__}: {exc}")
        return PublisherError(f"{type(exc).__name__}: {exc}")

    # -- API ---------------------------------------------------------------------
    def _translate_auth(self, exc: Exception) -> PublisherError:
        """Como ``_translate`` pero con el motivo concreto del fallo de sesión."""
        tw = self._tw
        text = str(exc)
        if isinstance(exc, getattr(tw, "AccountLocked", ())):
            return AuthError(
                "X ha bloqueado temporalmente la cuenta y pide una verificación (captcha).",
                AuthError.LOCKED,
            )
        if isinstance(exc, getattr(tw, "AccountSuspended", ())):
            return AuthError(f"La cuenta está suspendida: {text[:200]}", AuthError.SUSPENDED)
        if isinstance(exc, getattr(tw, "InvalidSession", ())):
            return AuthError("X trata la sesión como cerrada: cookies caducadas o revocadas")
        if isinstance(exc, getattr(tw, "Unauthorized", ())):
            return AuthError(
                "X rechaza las cookies (401): la sesión se cerró o caducó. "
                f"Detalle: {text[:200]}"
            )
        if isinstance(exc, getattr(tw, "Forbidden", ())):
            if "353" in text:
                return AuthError("ct0 no coincide con la sesión (403/353): copia de nuevo ct0")
            return AuthError(f"X deniega el acceso (403): {text[:200]}")
        return self._translate(exc)

    async def check_auth(self) -> str:
        """Comprueba la sesión y que sea de la cuenta esperada. Devuelve la arroba."""
        try:
            response, _ = await self._client.v11.settings()
        except Exception as exc:
            raise self._translate_auth(exc) from exc
        screen_name = response.get("screen_name") if isinstance(response, dict) else None
        if not screen_name:
            raise AuthError(
                "X no devuelve el usuario de la sesión (posible página de Cloudflare o "
                "sesión cerrada)"
            )
        if self._account and screen_name.lower() != self._account.lower():
            raise AuthError(
                f"Las cookies son de @{screen_name}, no de @{self._account}. "
                "Cópialas desde una sesión iniciada con la cuenta del bot."
            )
        return screen_name

    async def post(self, text: str, reply_to: str | None = None) -> str:
        try:
            tweet = await self._client.create_tweet(text=text, reply_to=reply_to)
        except Exception as exc:
            raise self._translate(exc) from exc
        tweet_id = str(getattr(tweet, "id", "") or "")
        if not tweet_id:
            raise PublisherError("X no devolvió el ID del tuit creado")
        return tweet_id

    def persist_cookies(self) -> None:
        """Guarda las cookies actualizadas (X rota ``ct0``) de forma atómica."""
        try:
            cookies = self._client.get_cookies()
            if not cookies.get("auth_token") or not cookies.get("ct0"):
                log.warning("El jar no contiene auth_token/ct0; no se sobrescriben las cookies")
                return
            target = self._cookies_path
            try:
                previous = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = None
            if previous == cookies:
                return
            if previous and previous.get("auth_token") != cookies.get("auth_token"):
                log.warning("X ha cambiado el auth_token de la sesión; se guarda el nuevo")
            if previous:
                # Copia de las cookies anteriores, por si las nuevas no funcionan.
                backup = target.with_name(target.name + ".bak")
                backup.write_text(json.dumps(previous), encoding="utf-8")
                os.chmod(backup, 0o600)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".cookies-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(cookies, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except Exception as exc:  # no es crítico
            log.warning("No se pudieron guardar las cookies actualizadas: %s", exc)

    async def close(self) -> None:
        for attr in ("http", "_curl_session"):
            session = getattr(self._client, attr, None)
            closer = getattr(session, "aclose", None) or getattr(session, "close", None)
            if closer is None:
                continue
            try:
                result = closer()
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # pragma: no cover
                pass
