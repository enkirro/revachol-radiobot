from __future__ import annotations

import json
import tempfile
from pathlib import Path

from revachol_radiobot.config import Settings

SAMPLE = [
    {"actorId": 1, "Languages": "Kim Kitsuragi", "dialogLong": "Right.", "Translation": "\"Bien\"."},
    {"actorId": 2, "Languages": "Tú", "dialogLong": "Makes sense.", "Translation": "\"Tiene sentido\"."},
    {"actorId": 2, "Languages": "Tú", "dialogLong": "Makes sense.", "Translation": "\"Tiene sentido\"."},
    {"actorId": 3, "Languages": "Lógica", "dialogLong": "...", "Translation": "Es elemental."},
    {"actorId": 4, "Languages": "Retórica", "dialogLong": "...", "Translation": ""},
    {"actorId": 5, "Languages": "Empatía", "dialogLong": "...", "Translation": " ".join(["muy larga"] * 400)},
]


def make_env(tmp: Path, data: list | None = None, **overrides: str) -> dict[str, str]:
    dataset = tmp / "dataset.json"
    dataset.write_text(json.dumps(SAMPLE if data is None else data, ensure_ascii=False), "utf-8")
    env = {
        "RADIOBOT_HOME": str(tmp),
        "RADIOBOT_ACCOUNT": "test_bot",
        "RADIOBOT_DATASET": str(dataset),
    }
    env.update(overrides)
    return env


def make_settings(tmp: Path, data: list | None = None, **overrides: str) -> Settings:
    return Settings.from_env(make_env(tmp, data, **overrides))


def tmpdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="revachol-radiobot-test-")
