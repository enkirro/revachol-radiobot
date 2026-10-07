"""Una ejecución del bot: elegir una entrada, publicarla (como hilo si hace falta)
y registrar el resultado. Pensado para lanzarse desde un timer de systemd.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum

from revachol_radiobot.config import Settings
from revachol_radiobot.dataset import Entry
from revachol_radiobot.notify import Notifier
from revachol_radiobot.publisher import (
    AuthError,
    Duplicate,
    Publisher,
    PublisherError,
    RateLimited,
    TransientError,
)
from revachol_radiobot.state import State
from revachol_radiobot.text import split_thread

log = logging.getLogger(__name__)

# Tipos de aviso de fallo. Cuando el bot vuelve a publicar tras alguno de
# ellos, se envía un aviso de recuperación.
FAILURE_KINDS = ("auth", "error", "ratelimit")

# Esperas entre reintentos de errores transitorios (red, 5xx).
RETRY_DELAYS = (30, 120)


def notify_once(
    state: State, notifier: Notifier, settings: Settings, kind: str, subject: str, body: str
) -> None:
    """Envía el aviso salvo que ya se haya enviado uno del mismo tipo hace poco."""
    if state.should_notify(kind, settings.notify_cooldown_hours):
        notifier.send(subject, body)
        state.mark_notified(kind)
    else:
        log.info("Aviso '%s' omitido (enfriamiento activo)", kind)


def auth_instructions(exc: AuthError, account: str) -> str:
    """Qué hacer según el motivo del fallo de sesión (texto para el aviso)."""
    if exc.reason == AuthError.LOCKED:
        return (
            "Qué hacer:\n"
            "1. Entra en x.com con la cuenta del bot y resuelve la verificación.\n"
            f"2. En el servidor: revachol-radiobot {account} check\n"
            "   Normalmente las cookies siguen valiendo y el bot vuelve a publicar solo "
            "en el siguiente turno.\n"
            f"3. Solo si check sigue fallando: revachol-radiobot {account} cookies set"
        )
    if exc.reason == AuthError.SUSPENDED:
        return (
            "Qué hacer: revisa la cuenta en x.com. Una suspensión no se arregla con "
            "cookies nuevas; el bot seguirá intentándolo en cada turno."
        )
    return (
        "Qué hacer: copia de nuevo auth_token y ct0 desde el navegador (con la sesión de "
        "la cuenta del bot iniciada) y ejecuta en el servidor, como root:\n"
        f"  revachol-radiobot {account} cookies set"
    )


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    AUTH = 2
    NOTHING_TO_POST = 3
    TEMPFAIL = 75  # EX_TEMPFAIL: rate limit; se reintentará en el siguiente turno


@dataclass
class RunResult:
    code: ExitCode
    message: str
    tweet_ids: list[str] = field(default_factory=list)


class _ThreadFailed(Exception):
    def __init__(self, posted_ids: list[str], cause: PublisherError) -> None:
        super().__init__(str(cause))
        self.posted_ids = posted_ids
        self.cause = cause


Sleep = Callable[[float], Awaitable[None]]


class Bot:
    def __init__(
        self,
        settings: Settings,
        state: State,
        entries: list[Entry],
        publisher: Publisher,
        notifier: Notifier,
        *,
        rng: random.Random | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.state = state
        self.entries = entries
        self.publisher = publisher
        self.notifier = notifier
        self.rng = rng or random.SystemRandom()
        self.sleep = sleep

    # -- selección -------------------------------------------------------------
    def pick(self) -> Entry | None:
        excluded = self.state.excluded_keys()
        candidates = [e for e in self.entries if e.key not in excluded]
        if not candidates and self.settings.recycle_when_exhausted:
            permanent = self.state.permanently_excluded()
            if any(e.key not in permanent for e in self.entries):
                cycle = self.state.start_new_cycle()
                log.info("Dataset agotado: empieza el ciclo %d", cycle)
                excluded = self.state.excluded_keys()
                candidates = [e for e in self.entries if e.key not in excluded]
        return self.rng.choice(candidates) if candidates else None

    # -- publicación -----------------------------------------------------------
    async def _post_with_retry(self, text: str, reply_to: str | None) -> str:
        retried = False
        for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
            try:
                return await self.publisher.post(text, reply_to=reply_to)
            except Duplicate:
                if retried:
                    # El intento anterior llegó a X pero se perdió la respuesta.
                    log.warning("Duplicado tras reintento: se asume publicado")
                    return ""
                raise
            except TransientError as exc:
                if delay is None:
                    raise
                retried = True
                log.warning(
                    "Error transitorio (intento %d): %s. Reintento en %ds", attempt, exc, delay
                )
                await self.sleep(delay)
        raise AssertionError("inalcanzable")

    async def _post_thread(self, parts: list[str]) -> list[str]:
        ids: list[str] = []
        reply_to: str | None = None
        for i, part in enumerate(parts):
            if i:
                await self.sleep(self.settings.thread_delay_seconds)
            try:
                tweet_id = await self._post_with_retry(part, reply_to)
            except PublisherError as exc:
                if not ids:
                    raise
                raise _ThreadFailed(ids, exc) from exc
            if tweet_id:
                ids.append(tweet_id)
                reply_to = tweet_id
            elif i < len(parts) - 1:
                raise _ThreadFailed(
                    ids, PublisherError("No hay ID del tuit anterior para continuar el hilo")
                )
        return ids

    # -- ejecución -------------------------------------------------------------
    def _notify(self, kind: str, subject: str, body: str) -> None:
        notify_once(self.state, self.notifier, self.settings, kind, subject, body)

    def notify_auth(self, exc: AuthError, *, while_posting: bool = False) -> None:
        subject = (
            "X ha rechazado la sesión al publicar" if while_posting
            else "X no acepta la sesión del bot"
        )
        self._notify("auth", subject, f"{exc}\n\n{auth_instructions(exc, self.settings.account)}")

    async def run_once(self) -> RunResult:
        result = await self._run()
        level = logging.INFO if result.code == ExitCode.OK else logging.ERROR
        log.log(level, "Resultado: %s - %s", result.code.name, result.message)
        return result

    async def _run(self) -> RunResult:
        try:
            await self.publisher.check_auth()
        except AuthError as exc:
            self.notify_auth(exc)
            return RunResult(ExitCode.AUTH, str(exc))
        except RateLimited as exc:
            self._notify_rate_limit(exc)
            return RunResult(ExitCode.TEMPFAIL, f"Rate limit al comprobar la sesión: {exc}")
        except PublisherError as exc:
            self._notify("error", "Error comprobando la sesión", str(exc))
            return RunResult(ExitCode.ERROR, str(exc))

        attempts = 0
        while attempts < self.settings.max_attempts:
            entry = self.pick()
            if entry is None:
                self._notify("exhausted", "No quedan entradas por publicar", "Revisa el dataset.")
                return RunResult(ExitCode.NOTHING_TO_POST, "No quedan entradas por publicar")

            parts = split_thread(
                entry.text,
                numbering=self.settings.thread_numbering,
                max_parts=self.settings.max_thread_parts,
            )
            if len(parts) > self.settings.max_thread_parts:
                self.state.record(
                    entry.key, entry.text, "skipped",
                    detail=f"necesita {len(parts)} tuits (máx {self.settings.max_thread_parts})",
                )
                log.info("Entrada omitida por longitud (%d partes)", len(parts))
                continue

            attempts += 1
            try:
                ids = await self._post_thread(parts)
            except Duplicate as exc:
                self.state.record(entry.key, entry.text, "rejected", detail=f"duplicado: {exc}")
                log.warning("X lo considera duplicado; se elige otra entrada")
                continue
            except _ThreadFailed as exc:
                self.state.record(entry.key, entry.text, "partial", exc.posted_ids, str(exc.cause))
                self._notify(
                    "error", "Hilo publicado a medias", f"{exc.cause}\nIDs: {exc.posted_ids}"
                )
                return self._error_result(exc.cause, exc.posted_ids)
            except AuthError as exc:
                self.notify_auth(exc, while_posting=True)
                return RunResult(ExitCode.AUTH, str(exc))
            except RateLimited as exc:
                self._notify_rate_limit(exc)
                return RunResult(ExitCode.TEMPFAIL, f"Rate limit: {exc}")
            except PublisherError as exc:
                self._notify("error", "Error publicando", f"{type(exc).__name__}: {exc}")
                return RunResult(ExitCode.ERROR, str(exc))

            self.state.record(entry.key, entry.text, "posted", ids)
            self.publisher.persist_cookies()
            summary = f"Publicado {ids or '(sin ID)'}: {entry.text[:120]}"
            self._notify_recovery(ids)
            return RunResult(ExitCode.OK, summary, ids)

        message = f"Sin éxito tras {attempts} intentos (todas las entradas rechazadas)"
        self._notify("error", "No se pudo publicar", message)
        return RunResult(ExitCode.ERROR, message)

    def _notify_rate_limit(self, exc: RateLimited) -> None:
        detail = str(exc)
        if exc.reset_at:
            reset = datetime.fromtimestamp(exc.reset_at).astimezone()
            detail += f"\nX levanta el límite hacia las {reset:%H:%M %Z}."
        self._notify(
            "ratelimit",
            "X ha limitado la cuenta",
            f"{detail}\nSe reintentará solo en los siguientes turnos.",
        )

    def _notify_recovery(self, ids: list[str]) -> None:
        pending = [k for k in FAILURE_KINDS if self.state.get_meta(f"notified:{k}")]
        for kind in FAILURE_KINDS:
            self.state.clear_notified(kind)
        if pending:
            link = (
                f"https://x.com/{self.settings.account}/status/{ids[0]}"
                if ids and not ids[0].startswith("dry-")
                else ""
            )
            self.notifier.send("Vuelve a publicar con normalidad", link, ok=True)

    @staticmethod
    def _error_result(cause: PublisherError, ids: list[str]) -> RunResult:
        if isinstance(cause, AuthError):
            code = ExitCode.AUTH
        elif isinstance(cause, RateLimited):
            code = ExitCode.TEMPFAIL
        else:
            code = ExitCode.ERROR
        return RunResult(code, f"Hilo incompleto ({ids}): {cause}", ids)
