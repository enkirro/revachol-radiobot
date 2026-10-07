"""Estado persistente en SQLite: qué se ha publicado, cuándo y con qué ID.

Sustituye al ``random.choice`` del bot original, que podía repetir entradas
(y X rechaza contenido duplicado). Aquí se elige al azar solo entre las
entradas que aún no se han publicado en el ciclo actual; cuando se agotan,
empieza un ciclo nuevo.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_key  TEXT    NOT NULL,
    cycle      INTEGER NOT NULL,
    status     TEXT    NOT NULL CHECK (status IN ('posted', 'partial', 'skipped', 'rejected')),
    tweet_ids  TEXT    NOT NULL DEFAULT '[]',
    text       TEXT    NOT NULL,
    detail     TEXT,
    created_at TEXT    NOT NULL,
    UNIQUE (entry_key, cycle)
);
CREATE INDEX IF NOT EXISTS posts_cycle ON posts (cycle);
CREATE INDEX IF NOT EXISTS posts_status ON posts (status);
"""

# Estados que excluyen la entrada para siempre (no solo en el ciclo actual).
PERMANENT_EXCLUSIONS = ("skipped", "rejected")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Stats:
    cycle: int
    total_entries: int
    used_this_cycle: int
    remaining_this_cycle: int
    posted_all_time: int
    skipped: int
    rejected: int
    last_post_at: str | None
    last_post_text: str | None


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)
        if self.get_meta("schema_version") is None:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.set_meta("cycle", "1")

    @classmethod
    def in_memory(cls) -> State:
        return cls(Path(":memory:"))

    def snapshot(self) -> State:
        """Copia en memoria del estado actual (para simulaciones)."""
        copy = State.in_memory()
        self._db.backup(copy._db)
        return copy

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- meta ---------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    @property
    def cycle(self) -> int:
        return int(self.get_meta("cycle") or 1)

    def start_new_cycle(self) -> int:
        new = self.cycle + 1
        self.set_meta("cycle", str(new))
        return new

    # -- selección ------------------------------------------------------------
    def excluded_keys(self) -> set[str]:
        """Claves que no se pueden elegir ahora: usadas en este ciclo o vetadas."""
        placeholders = ",".join("?" * len(PERMANENT_EXCLUSIONS))
        rows = self._db.execute(
            f"SELECT entry_key FROM posts WHERE cycle = ? OR status IN ({placeholders})",
            (self.cycle, *PERMANENT_EXCLUSIONS),
        )
        return {r[0] for r in rows}

    def permanently_excluded(self) -> set[str]:
        placeholders = ",".join("?" * len(PERMANENT_EXCLUSIONS))
        rows = self._db.execute(
            f"SELECT entry_key FROM posts WHERE status IN ({placeholders})",
            PERMANENT_EXCLUSIONS,
        )
        return {r[0] for r in rows}

    # -- registro -------------------------------------------------------------
    def record(
        self,
        entry_key: str,
        text: str,
        status: str,
        tweet_ids: Iterable[str] = (),
        detail: str | None = None,
        *,
        cycle: int | None = None,
        created_at: str | None = None,
    ) -> None:
        self._db.execute(
            "INSERT INTO posts (entry_key, cycle, status, tweet_ids, text, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(entry_key, cycle) DO UPDATE SET status = excluded.status, "
            "tweet_ids = excluded.tweet_ids, detail = excluded.detail, "
            "created_at = excluded.created_at",
            (
                entry_key,
                self.cycle if cycle is None else cycle,
                status,
                json.dumps(list(tweet_ids)),
                text,
                detail,
                created_at or _now(),
            ),
        )

    def bulk_import(self, rows: Iterable[tuple[str, str, str | None]], *, cycle: int) -> int:
        """Importa ``(entry_key, text, tweet_id)`` como publicados. Ignora repetidos."""
        cur = self._db.cursor()
        cur.execute("BEGIN")
        n = 0
        for key, text, tweet_id in rows:
            cur.execute(
                "INSERT OR IGNORE INTO posts "
                "(entry_key, cycle, status, tweet_ids, text, detail, created_at) "
                "VALUES (?, ?, 'posted', ?, ?, 'importado del log antiguo', ?)",
                (key, cycle, json.dumps([tweet_id] if tweet_id else []), text, _now()),
            )
            n += cur.rowcount
        cur.execute("COMMIT")
        return n

    # -- avisos ---------------------------------------------------------------
    def should_notify(self, kind: str, cooldown_hours: int) -> bool:
        last = self.get_meta(f"notified:{kind}")
        if last is None:
            return True
        elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        return elapsed.total_seconds() >= cooldown_hours * 3600

    def mark_notified(self, kind: str) -> None:
        self.set_meta(f"notified:{kind}", _now())

    def clear_notified(self, kind: str) -> None:
        self._db.execute("DELETE FROM meta WHERE key = ?", (f"notified:{kind}",))

    # -- informes -------------------------------------------------------------
    def stats(self, total_entries: int, valid_keys: set[str] | None = None) -> Stats:
        cycle = self.cycle
        excluded = self.excluded_keys()
        if valid_keys is not None:
            excluded &= valid_keys
        with closing(self._db.cursor()) as cur:
            used = cur.execute(
                "SELECT COUNT(*) FROM posts WHERE cycle = ?", (cycle,)
            ).fetchone()[0]
            counts = dict(
                cur.execute("SELECT status, COUNT(*) FROM posts GROUP BY status").fetchall()
            )
            last = cur.execute(
                "SELECT created_at, text FROM posts WHERE status IN ('posted', 'partial') "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return Stats(
            cycle=cycle,
            total_entries=total_entries,
            used_this_cycle=used,
            remaining_this_cycle=max(total_entries - len(excluded), 0),
            posted_all_time=counts.get("posted", 0) + counts.get("partial", 0),
            skipped=counts.get("skipped", 0),
            rejected=counts.get("rejected", 0),
            last_post_at=last[0] if last else None,
            last_post_text=last[1] if last else None,
        )
