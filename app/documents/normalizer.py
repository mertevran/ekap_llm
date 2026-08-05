from __future__ import annotations

import html
import re

HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
INLINE_WHITESPACE_PATTERN = re.compile(r"[ \t\f\v]+")
MULTIPLE_NEWLINES_PATTERN = re.compile(r"\n{3,}")


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""

    text = html.unescape(str(value))

    # Bölünemez boşlukları normal boşluğa dönüştür.
    text = text.replace("\u00a0", " ")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = HTML_TAG_PATTERN.sub(" ", text)

    normalized_lines: list[str] = []

    for raw_line in text.splitlines():
        line = INLINE_WHITESPACE_PATTERN.sub(" ", raw_line).strip()

        if line:
            normalized_lines.append(line)
        elif normalized_lines and normalized_lines[-1] != "":
            normalized_lines.append("")

    normalized = "\n".join(normalized_lines).strip()
    normalized = MULTIPLE_NEWLINES_PATTERN.sub("\n\n", normalized)

    return normalized
