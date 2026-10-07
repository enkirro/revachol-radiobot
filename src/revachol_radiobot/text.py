"""Longitud de tuit al estilo de X y división en hilos.

X no cuenta caracteres, cuenta "peso" (twitter-text v3): la mayoría de
caracteres latinos pesan 1, pero emojis, CJK y otros pesan 2, y cada URL
cuenta como 23 sin importar su longitud. El bot anterior usaba ``len()``,
que subestima tuits con emojis o comillas tipográficas especiales.
"""

from __future__ import annotations

import re
import unicodedata

MAX_WEIGHTED_LENGTH = 280
URL_WEIGHT = 23

# Rangos con peso 1 según twitter-text v3 (config/v3.json). El resto pesa 2.
_LIGHT_RANGES = (
    (0x0000, 0x10FF),
    (0x2000, 0x200D),
    (0x2010, 0x201F),
    (0x2032, 0x2037),
)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
# Puntos de corte, de mejor a peor. Las comillas o paréntesis de cierre se
# quedan con su frase: `"Bien". El teniente` se corta tras `".`
_CLOSERS = "\"'»”’)\\]"
_SENTENCE_END_RE = re.compile(rf"[.!?…]+[{_CLOSERS}]*(?=\s)")
_CLAUSE_END_RE = re.compile(rf"[,;:—–][{_CLOSERS}]*(?=\s)")

# Niveles de corte: 0 = solo fin de frase, 1 = también fin de cláusula, 2 = cualquier espacio.
SENTENCE, CLAUSE, WORD = 0, 1, 2


def _char_weight(ch: str) -> int:
    cp = ord(ch)
    for lo, hi in _LIGHT_RANGES:
        if lo <= cp <= hi:
            return 1
    return 2


def weighted_length(text: str) -> int:
    """Longitud ponderada de ``text`` tal y como la calcula X."""
    text = unicodedata.normalize("NFC", text)
    total = 0
    pos = 0
    for match in _URL_RE.finditer(text):
        total += sum(_char_weight(c) for c in text[pos : match.start()])
        total += URL_WEIGHT
        pos = match.end()
    total += sum(_char_weight(c) for c in text[pos:])
    return total


def fits(text: str, limit: int = MAX_WEIGHTED_LENGTH) -> bool:
    return weighted_length(text) <= limit


def normalize(text: str) -> str:
    """NFC + espacios colapsados. Evita tuits con saltos o dobles espacios raros."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def _cut_index(text: str, budget: int, level: int) -> int:
    """Índice (exclusivo) donde cortar ``text`` sin superar ``budget``.

    Busca el último corte permitido por ``level`` que quepa. Si no hay
    ninguno (p. ej. una sola frase más larga que un tuit), baja de nivel:
    frase → cláusula → palabra → corte duro (solo si una palabra no cabe).
    """
    weight = 0
    hard = len(text)
    for i, ch in enumerate(text):
        weight += _char_weight(ch)
        if weight > budget:
            hard = i
            break
    else:
        return len(text)

    window = text[: hard + 1]  # incluye el carácter siguiente para la anticipación (?=\s)
    patterns = (_SENTENCE_END_RE, _CLAUSE_END_RE)
    for lvl in range(level, CLAUSE + 1):
        ends = [m.end() for p in patterns[: lvl + 1] for m in p.finditer(window)]
        ends = [e for e in ends if e <= hard]
        if ends:
            return max(ends)
    space = window.rfind(" ", 0, hard + 1)
    if space > 0:
        return space
    return max(hard, 1)


def _split(text: str, budget: int, level: int) -> list[str]:
    parts: list[str] = []
    rest = text
    while rest:
        idx = _cut_index(rest, budget, level)
        chunk = rest[:idx].strip()
        if chunk:
            parts.append(chunk)
        rest = rest[idx:].strip()
    return parts


def _split_numbered(text: str, limit: int, level: int) -> list[str]:
    # El sufijo " (i/n)" depende de n; se recalcula hasta que n es estable.
    total = 2
    for _ in range(5):
        parts = _split(text, limit - len(f" ({total}/{total})"), level)
        if len(parts) == total:
            break
        total = len(parts)
    n = len(parts)
    return [f"{p} ({i}/{n})" for i, p in enumerate(parts, start=1)]


def split_thread(
    text: str,
    *,
    limit: int = MAX_WEIGHTED_LENGTH,
    numbering: bool = True,
    max_parts: int | None = None,
) -> list[str]:
    """Divide ``text`` en fragmentos que caben en un tuit cada uno.

    Primero intenta cortar solo entre frases. Si así salen más de
    ``max_parts`` tuits, prueba cortando también en comas/punto y coma, y en
    último caso entre palabras. Nunca corta una palabra salvo que por sí sola
    supere el límite. Con ``numbering`` añade `` (1/2)`` a cada fragmento.
    """
    text = normalize(text)
    if not text:
        return []
    if fits(text, limit):
        return [text]

    best: list[str] = []
    for level in (SENTENCE, CLAUSE, WORD):
        if numbering:
            parts = _split_numbered(text, limit, level)
        else:
            parts = _split(text, limit, level)
        if not best or len(parts) < len(best):
            best = parts
        if max_parts is None or len(parts) <= max_parts:
            return parts
    return best
