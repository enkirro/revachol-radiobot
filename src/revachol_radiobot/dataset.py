"""Carga del dataset JSON y construcción del texto de cada entrada."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from revachol_radiobot.text import normalize


class DatasetError(RuntimeError):
    """El dataset no existe, no es JSON válido o no tiene el formato esperado."""


@dataclass(frozen=True)
class Entry:
    """Una entrada publicable. ``key`` identifica el texto de forma estable."""

    key: str
    text: str
    index: int


def fingerprint(text: str) -> str:
    """Huella estable del texto final (no del índice): resiste reordenar el JSON."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:32]


def render(record: dict, *, author_field: str, text_field: str, template: str) -> str | None:
    author = record.get(author_field)
    body = record.get(text_field)
    if not isinstance(body, str) or not body.strip():
        return None
    author = author.strip() if isinstance(author, str) else ""
    if not author and "{author}" in template:
        return normalize(body)
    return normalize(template.format(author=author, text=body.strip()))


def load_entries(
    path: Path,
    *,
    author_field: str = "Languages",
    text_field: str = "Translation",
    template: str = "{author}: {text}",
) -> list[Entry]:
    """Devuelve las entradas válidas, sin duplicados (por texto final)."""
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise DatasetError(f"No existe el dataset: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetError(f"Dataset JSON inválido ({path}): {exc}") from exc

    if not isinstance(data, list):
        raise DatasetError("El dataset debe ser una lista JSON de objetos")

    seen: set[str] = set()
    entries: list[Entry] = []
    for i, record in enumerate(data):
        if not isinstance(record, dict):
            continue
        text = render(record, author_field=author_field, text_field=text_field, template=template)
        if not text:
            continue
        key = fingerprint(text)
        if key in seen:
            continue
        seen.add(key)
        entries.append(Entry(key=key, text=text, index=i))
    if not entries:
        raise DatasetError("El dataset no contiene entradas publicables")
    return entries
