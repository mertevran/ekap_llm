from __future__ import annotations

import re
import unicodedata
from typing import Any

_HORIZONTAL_SPACE = re.compile(r"[ \t\f\v]+")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\xa0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.split("\n")]

    normalized = "\n".join(lines)
    normalized = _EXCESS_BLANK_LINES.sub("\n\n", normalized)
    return normalized.strip()


def display_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "Evet" if value else "Hayır"

    normalized = normalize_text(value)
    return normalized or "-"


def iso_value(value: Any) -> str | None:
    if value is None:
        return None

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()

    normalized = normalize_text(value)
    return normalized or None
