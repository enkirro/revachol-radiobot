"""Interfaz de línea de comandos: ``revachol-radiobot <comando>``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import logging
import os
import random
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from revachol_radiobot import __version__
from revachol_radiobot.bot import Bot, ExitCode, auth_instructions, notify_once
from revachol_radiobot.config import ConfigError, Settings, load_env_file
from revachol_radiobot.dataset import DatasetError, load_entries
from revachol_radiobot.notify import Notifier
from revachol_radiobot.publisher import AuthError, DryRunPublisher, PublisherError, TwikitPublisher
from revachol_radiobot.state import State
from revachol_radiobot.text import split_thread, weighted_length

log = logging.getLogger("revachol-radiobot")


def _setup_logging(verbose: bool) -> None:
    # Bajo systemd el journal ya pone fecha y hora; en terminal la añadimos.
    under_systemd = "INVOCATION_ID" in os.environ
    fmt = "%(levelname)s %(name)s: %(message)s"
    if not under_systemd:
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not verbose:
        for noisy in ("httpx", "httpcore", "twikit", "curl_cffi"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


@contextlib.contextmanager
def _single_instance(lock_path: Path) -> Iterator[None]:
    """Evita dos ejecuciones simultáneas (p. ej. una manual y la del timer)."""
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Ya hay otra ejecución de revachol-radiobot en curso") from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _entries(settings: Settings):
    return load_entries(
        settings.dataset_path,
        author_field=settings.author_field,
        text_field=settings.text_field,
        template=settings.template,
    )


# -- comandos -----------------------------------------------------------------
def cmd_post(settings: Settings, args: argparse.Namespace) -> int:
    dry_run = args.dry_run or settings.dry_run
    entries = _entries(settings)
    with _single_instance(settings.lock_path), State(settings.state_db) as state:
        if dry_run:
            publisher = DryRunPublisher()
        else:
            try:
                publisher = TwikitPublisher(
                    settings.cookies_path,
                    language=settings.language,
                    impersonate=settings.impersonate,
                    proxy=settings.proxy,
                    account=settings.account,
                    user_agent=settings.user_agent,
                )
            except PublisherError as exc:
                log.error("%s", exc)
                is_auth = isinstance(exc, AuthError)
                body = str(exc)
                if is_auth:
                    body += f"\n\n{auth_instructions(exc, settings.account)}"
                notify_once(
                    state, Notifier(settings), settings,
                    "auth" if is_auth else "error",
                    "No se puede iniciar el cliente de X", body,
                )
                return ExitCode.AUTH if is_auth else ExitCode.ERROR

        async def run() -> int:
            try:
                if dry_run:
                    # Ni avisos ni estado real: se trabaja sobre una copia en memoria.
                    quiet = replace(settings, telegram=None)
                    with state.snapshot() as scratch:
                        bot = Bot(quiet, scratch, entries, publisher, Notifier(quiet))
                        return (await bot.run_once()).code
                bot = Bot(settings, state, entries, publisher, Notifier(settings))
                return (await bot.run_once()).code
            finally:
                await publisher.close()

        return int(asyncio.run(run()))


def cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    ok = True
    try:
        entries = _entries(settings)
        print(f"✔ Dataset: {len(entries)} entradas únicas en {settings.dataset_path}")
    except DatasetError as exc:
        print(f"✘ Dataset: {exc}")
        ok = False

    path = settings.cookies_path
    if not path.exists():
        print(f"✘ Cookies: no existe {path}")
        print(f"  Guárdalas con: revachol-radiobot {settings.account} cookies set")
        return 1
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        print(f"⚠ Cookies: permisos {oct(mode)}; deberían ser 0600")
    print(f"✔ Cookies: {path}")

    if args.offline:
        return 0 if ok else 1

    async def probe() -> str:
        pub = TwikitPublisher(
            path, language=settings.language, impersonate=settings.impersonate,
            proxy=settings.proxy, account=settings.account, user_agent=settings.user_agent,
        )
        try:
            return await pub.check_auth()
        finally:
            await pub.close()

    try:
        screen_name = asyncio.run(probe())
        print(f"✔ Sesión de X válida (@{screen_name})")
    except PublisherError as exc:
        print(f"✘ Sesión de X: {exc}")
        ok = False
    return 0 if ok else 1


def cmd_stats(settings: Settings, args: argparse.Namespace) -> int:
    entries = _entries(settings)
    with State(settings.state_db) as state:
        s = state.stats(len(entries), {e.key for e in entries})
    if args.json:
        print(json.dumps(s.__dict__, ensure_ascii=False, indent=2))
        return 0
    print(f"Cuenta:                  @{settings.account}  ({settings.home})")
    print(f"Ciclo actual:            {s.cycle}")
    print(f"Entradas en el dataset:  {s.total_entries}")
    print(f"Quedan en este ciclo:    {s.remaining_this_cycle}")
    print(f"Publicados (histórico):  {s.posted_all_time}")
    print(f"Omitidos por longitud:   {s.skipped}")
    print(f"Rechazados por X:        {s.rejected}")
    if s.last_post_at:
        # En la base de datos se guarda en UTC; aquí se muestra en la hora del sistema.
        local = datetime.fromisoformat(s.last_post_at).astimezone()
        when = local.strftime("%Y-%m-%d %H:%M:%S %Z")
        print(f"Último tuit:             {when}  {s.last_post_text[:80]!r}")
    if s.remaining_this_cycle:
        days = s.remaining_this_cycle * 1.5 / 24
        print(f"Autonomía a 90 min:      ~{days:,.0f} días")
    return 0


def cmd_preview(settings: Settings, args: argparse.Namespace) -> int:
    entries = _entries(settings)
    rng = random.Random(args.seed)
    for entry in rng.sample(entries, min(args.count, len(entries))):
        parts = split_thread(
            entry.text,
            numbering=settings.thread_numbering,
            max_parts=settings.max_thread_parts,
        )
        flag = " (se omitiría)" if len(parts) > settings.max_thread_parts else ""
        print(f"--- {len(parts)} tuit(s){flag}")
        for p in parts:
            print(f"[{weighted_length(p):3d}] {p}")
    return 0


def cmd_cookies_set(settings: Settings, args: argparse.Namespace) -> int:
    if args.stdin:
        # Modo usado por /usr/local/bin/revachol-radiobot: dos líneas, auth_token y ct0.
        lines = sys.stdin.read().splitlines() + ["", ""]
        auth_token, ct0 = lines[0].strip(), lines[1].strip()
    else:
        print("Copia los valores desde el navegador (DevTools → Application → Cookies).")
        auth_token = getpass.getpass("auth_token: ").strip()
        ct0 = getpass.getpass("ct0: ").strip()
    if not auth_token or not ct0:
        print("Hacen falta auth_token y ct0")
        return 1
    path = settings.cookies_path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"auth_token": auth_token, "ct0": ct0}, fh)
    os.chmod(path, 0o600)
    print(f"Cookies guardadas en {path}. Comprueba con: revachol-radiobot {settings.account} check")
    return 0


def cmd_import_log(settings: Settings, args: argparse.Namespace) -> int:
    from revachol_radiobot.legacy import match_log

    entries = _entries(settings)
    matches, report = match_log(Path(args.logfile), entries)
    with _single_instance(settings.lock_path), State(settings.state_db) as state:
        inserted = state.bulk_import(
            ((m.entry.key, m.entry.text, m.tweet_id) for m in matches), cycle=state.cycle
        )
    print(
        f"Líneas de tuit en el log: {report.lines}\n"
        f"Reconocidas en el dataset: {report.matched} (no reconocidas: {report.unmatched})\n"
        f"Marcadas como ya publicadas: {inserted}"
    )
    return 0


def cmd_notify_test(settings: Settings, args: argparse.Namespace) -> int:
    notifier = Notifier(settings)
    if not notifier.enabled:
        print("Telegram no está configurado (RADIOBOT_TELEGRAM_BOT_TOKEN y _CHAT_ID).")
        return 1
    ok = notifier.send(
        "Prueba de avisos",
        "Si lees esto, los avisos de revachol-radiobot llegan bien.",
        ok=True,
    )
    print("✔ Enviado (Telegram)" if ok else "✘ Falló (Telegram): revisa el log de arriba")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="revachol-radiobot", description=__doc__)
    p.add_argument("--version", action="version", version=f"revachol-radiobot {__version__}")
    p.add_argument(
        "--env-file",
        type=Path,
        action="append",
        default=[],
        help="Fichero CLAVE=valor (repetible; el primero tiene prioridad)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("post", help="Publica una entrada (lo que ejecuta el timer)")
    sp.add_argument("--dry-run", action="store_true", help="No publica ni modifica el estado")
    sp.set_defaults(func=cmd_post)

    sp = sub.add_parser("check", help="Comprueba dataset, cookies y sesión de X")
    sp.add_argument("--offline", action="store_true", help="No contacta con X")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("stats", help="Progreso y último tuit")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("preview", help="Muestra entradas al azar tal y como se publicarían")
    sp.add_argument("-n", "--count", type=int, default=5)
    sp.add_argument("--seed", type=int)
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("notify-test", help="Envía un aviso de prueba")
    sp.set_defaults(func=cmd_notify_test)

    sp = sub.add_parser("cookies", help="Gestiona las cookies de sesión")
    csub = sp.add_subparsers(dest="cookies_command", required=True)
    cs = csub.add_parser("set", help="Guarda auth_token y ct0 (se piden sin eco)")
    cs.add_argument("--stdin", action="store_true", help="Lee auth_token y ct0 de stdin")
    cs.set_defaults(func=cmd_cookies_set)

    sp = sub.add_parser("import-log", help="Importa el historial del bot antiguo")
    sp.add_argument("logfile")
    sp.set_defaults(func=cmd_import_log)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.umask(0o077)  # estado, cookies y lock solo legibles por el usuario de servicio
    _setup_logging(args.verbose)
    try:
        for env_file in args.env_file:
            load_env_file(env_file)
        settings = Settings.from_env()
        return int(args.func(settings, args))
    except (ConfigError, DatasetError) as exc:
        log.error("%s", exc)
        return ExitCode.ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
