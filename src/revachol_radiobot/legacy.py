"""Importa el historial del bot antiguo (``cron_log.log``) para no repetir tuits."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from revachol_radiobot.dataset import Entry
from revachol_radiobot.text import normalize

_LINE_RE = re.compile(r"^Tweet ID: (\d+) - Tweeted(?: \(part (\d+)\))?: (.*)$")


@dataclass(frozen=True)
class LegacyMatch:
    entry: Entry
    tweet_id: str


@dataclass
class ImportReport:
    lines: int = 0
    matched: int = 0
    unmatched: int = 0


def parse_log(path: Path) -> Iterator[tuple[str, int | None, str]]:
    """Devuelve ``(tweet_id, parte, texto)`` por cada tuit registrado en el log."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _LINE_RE.match(line.rstrip("\n"))
            if m:
                part = int(m.group(2)) if m.group(2) else None
                yield m.group(1), part, m.group(3)


def match_log(path: Path, entries: list[Entry]) -> tuple[list[LegacyMatch], ImportReport]:
    by_text = {e.text: e for e in entries}
    long_entries = [e for e in entries if len(e.text) > 280]
    report = ImportReport()
    matches: list[LegacyMatch] = []
    for tweet_id, part, raw in parse_log(path):
        if part not in (None, 1):
            continue  # la parte 2 pertenece a la misma entrada que la 1
        report.lines += 1
        text = normalize(raw)
        entry = by_text.get(text)
        if entry is None and part == 1:
            entry = next((e for e in long_entries if e.text.startswith(text)), None)
        if entry is None:
            report.unmatched += 1
            continue
        report.matched += 1
        matches.append(LegacyMatch(entry, tweet_id))
    return matches, report
