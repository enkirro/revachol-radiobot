"""Configuración leída de variables de entorno (prefijo ``RADIOBOT_``).

Cada bot vive en su propia carpeta (``RADIOBOT_HOME``), normalmente
``/opt/revachol-radiobot/bots/<cuenta>``, con su ``bot.env``, dataset, cookies y
estado. Los valores comunes a todos los bots (p. ej. Telegram) pueden ir en
``/opt/revachol-radiobot/common.env``; lo que defina ``bot.env`` tiene prioridad.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


# Las arrobas de X: 1-15 caracteres alfanuméricos o guion bajo.
ACCOUNT_RE = re.compile(r"[A-Za-z0-9_]{1,15}")


def default_user_agent(impersonate: str | None) -> str | None:
    """User-Agent coherente con la huella TLS que se imita.

    twikit envía por defecto un User-Agent de Safari en macOS; combinado con
    la huella TLS de Chrome (``impersonate=chrome124``) es una incoherencia
    fácil de detectar como bot. Si se imita Chrome N, se anuncia Chrome N.
    """
    m = re.fullmatch(r"chrome(\d+)\w*", impersonate or "")
    if not m:
        return None
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{m.group(1)}.0.0.0 Safari/537.36"
    )


class ConfigError(ValueError):
    """Configuración inválida o incompleta."""


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _int(name: str, value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un entero, recibido {value!r}") from exc


def load_env_file(path: Path) -> None:
    """Carga un fichero ``CLAVE=valor`` en ``os.environ`` sin pisar lo ya definido."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    # Chat privado: 123456789. Grupo/supergrupo: -100xxxxxxxxxx. Canal público: @nombre.
    chat_id: str
    # Tema (topic) de un supergrupo con temas activados. None = "General".
    thread_id: int | None = None

    def __repr__(self) -> str:  # que el token no acabe en logs ni trazas
        return (
            f"TelegramConfig(token='***', chat_id={self.chat_id!r}, thread_id={self.thread_id!r})"
        )


@dataclass(frozen=True)
class Settings:
    account: str
    home: Path
    dataset_path: Path
    state_db: Path
    cookies_path: Path
    lock_path: Path

    # Formato del tuit: campos del JSON a usar.
    author_field: str = "Languages"
    text_field: str = "Translation"
    template: str = "{author}: {text}"

    # Hilos para textos que no caben en un tuit.
    max_thread_parts: int = 2
    thread_numbering: bool = True
    thread_delay_seconds: int = 8

    # Cliente X.
    language: str = "es-ES"
    impersonate: str | None = "chrome124"
    user_agent: str | None = None
    proxy: str | None = None

    # Comportamiento.
    dry_run: bool = False
    recycle_when_exhausted: bool = True
    max_attempts: int = 3

    # Avisos.
    notify_cooldown_hours: int = 12
    telegram: TelegramConfig | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)
        g = e.get

        # Carpeta del bot: /opt/revachol-radiobot/bots/<cuenta> en producción.
        home = Path(g("RADIOBOT_HOME") or ".").expanduser()
        account = (g("RADIOBOT_ACCOUNT") or home.resolve().name).lstrip("@")

        telegram = None
        tg_token, tg_chat = g("RADIOBOT_TELEGRAM_BOT_TOKEN"), g("RADIOBOT_TELEGRAM_CHAT_ID")
        if tg_token or tg_chat:
            if not (tg_token and tg_chat):
                raise ConfigError(
                    "Telegram necesita RADIOBOT_TELEGRAM_BOT_TOKEN y RADIOBOT_TELEGRAM_CHAT_ID"
                )
            tg_chat = tg_chat.strip()
            if not re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,}", tg_chat):
                raise ConfigError(
                    f"RADIOBOT_TELEGRAM_CHAT_ID no válido: {tg_chat!r} "
                    "(un supergrupo empieza por -100, p. ej. -1001234567890)"
                )
            thread = g("RADIOBOT_TELEGRAM_THREAD_ID")
            telegram = TelegramConfig(
                token=tg_token.strip(),
                chat_id=tg_chat,
                thread_id=_int("RADIOBOT_TELEGRAM_THREAD_ID", thread, 0) or None,
            )

        impersonate = g("RADIOBOT_IMPERSONATE", "chrome124")
        settings = cls(
            account=account,
            home=home,
            dataset_path=Path(g("RADIOBOT_DATASET") or home / "dataset.json"),
            state_db=Path(g("RADIOBOT_STATE_DB") or home / "state.db"),
            cookies_path=Path(g("RADIOBOT_COOKIES") or home / "cookies.json"),
            lock_path=Path(g("RADIOBOT_LOCK") or home / ".lock"),
            author_field=g("RADIOBOT_AUTHOR_FIELD") or "Languages",
            text_field=g("RADIOBOT_TEXT_FIELD") or "Translation",
            template=g("RADIOBOT_TEMPLATE") or "{author}: {text}",
            max_thread_parts=_int(
                "RADIOBOT_MAX_THREAD_PARTS", g("RADIOBOT_MAX_THREAD_PARTS"), 2
            ),
            thread_numbering=_bool(g("RADIOBOT_THREAD_NUMBERING"), True),
            thread_delay_seconds=_int("RADIOBOT_THREAD_DELAY", g("RADIOBOT_THREAD_DELAY"), 8),
            language=g("RADIOBOT_LANGUAGE") or "es-ES",
            impersonate=impersonate or None,
            user_agent=g("RADIOBOT_USER_AGENT") or default_user_agent(impersonate),
            proxy=g("RADIOBOT_PROXY") or None,
            dry_run=_bool(g("RADIOBOT_DRY_RUN"), False),
            recycle_when_exhausted=_bool(g("RADIOBOT_RECYCLE"), True),
            max_attempts=_int("RADIOBOT_MAX_ATTEMPTS", g("RADIOBOT_MAX_ATTEMPTS"), 3),
            notify_cooldown_hours=_int(
                "RADIOBOT_NOTIFY_COOLDOWN_HOURS", g("RADIOBOT_NOTIFY_COOLDOWN_HOURS"), 12
            ),
            telegram=telegram,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not ACCOUNT_RE.fullmatch(self.account):
            raise ConfigError(
                f"Nombre de cuenta no válido: {self.account!r} "
                "(1-15 caracteres: letras, números y _)"
            )
        if self.max_thread_parts < 1:
            raise ConfigError("RADIOBOT_MAX_THREAD_PARTS debe ser >= 1")
        if self.max_attempts < 1:
            raise ConfigError("RADIOBOT_MAX_ATTEMPTS debe ser >= 1")
        if "{text}" not in self.template:
            raise ConfigError("RADIOBOT_TEMPLATE debe contener {text}")
