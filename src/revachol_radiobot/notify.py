"""Avisos al operador por Telegram (chat privado, grupo o tema de un supergrupo).

Solo usa la librería estándar para que un fallo aquí nunca impida publicar.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request

from revachol_radiobot.config import Settings

log = logging.getLogger(__name__)

_TIMEOUT = 15


def _request(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> None:
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 (URLs de config)
        resp.read()


def _telegram_hint(exc: urllib.error.HTTPError) -> str:
    """Traduce los errores habituales de Telegram en grupos a algo accionable."""
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
    except (ValueError, OSError):
        return str(exc)
    desc = str(payload.get("description", ""))
    params = payload.get("parameters") or {}
    if "migrate_to_chat_id" in params:
        return (
            f"{desc}. El grupo se convirtió en supergrupo: usa "
            f"RADIOBOT_TELEGRAM_CHAT_ID={params['migrate_to_chat_id']}"
        )
    lowered = desc.lower()
    if "thread not found" in lowered or "topic" in lowered:
        return f"{desc}. Revisa RADIOBOT_TELEGRAM_THREAD_ID (el tema existe y está abierto)."
    if "chat not found" in lowered:
        return f"{desc}. Revisa el chat_id (-100...) y que el bot siga en el grupo."
    if "not enough rights" in lowered or "forbidden" in lowered:
        return f"{desc}. El bot necesita permiso para escribir en el grupo/tema."
    return desc or str(exc)


class Notifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.host = socket.gethostname()

    @property
    def enabled(self) -> bool:
        return self.settings.telegram is not None

    def send(self, subject: str, body: str, *, ok: bool = False) -> bool:
        """Envía el aviso por Telegram. Devuelve True si se entregó."""
        if not self.enabled:
            log.info("Aviso (Telegram no configurado): %s - %s", subject, body)
            return False
        return self._telegram(subject, body, ok)

    def _telegram(self, subject: str, body: str, ok: bool) -> bool:
        tg = self.settings.telegram
        assert tg is not None
        icon = "✅" if ok else "⚠️"
        text = f"{icon} @{self.settings.account} ({self.host})\n{subject}"
        if body:
            text += f"\n\n{body}"
        fields = {
            "chat_id": tg.chat_id,
            "text": text[:4000],
            "disable_web_page_preview": "true",
        }
        if tg.thread_id:
            fields["message_thread_id"] = str(tg.thread_id)
        data = urllib.parse.urlencode(fields).encode()
        url = f"https://api.telegram.org/bot{tg.token}/sendMessage"
        try:
            _request(url, data, {"Content-Type": "application/x-www-form-urlencoded"})
            return True
        except urllib.error.HTTPError as exc:
            # No registrar la URL: contiene el token del bot.
            log.error("Telegram rechazó el aviso (HTTP %s): %s", exc.code, _telegram_hint(exc))
        except (urllib.error.URLError, OSError) as exc:
            log.error("No se pudo enviar el aviso por Telegram: %s", getattr(exc, "reason", exc))
        return False
