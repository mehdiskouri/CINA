from __future__ import annotations

import re

ABBREVIATIONS = {
    "i.v.",
    "p.o.",
    "b.i.d.",
    "t.i.d.",
    "q.i.d.",
    "dr.",
    "fig.",
    "et al.",
    "vs.",
    "mr.",
    "mrs.",
    "ms.",
}

_SPLIT_PATTERN = re.compile(r"([.!?])\s+")


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    sentences: list[str] = []
    start = 0
    for match in _SPLIT_PATTERN.finditer(normalized):
        end = match.end()
        candidate = normalized[start:end].strip()
        lowered = candidate.lower()
        if any(lowered.endswith(abbrev) for abbrev in ABBREVIATIONS):
            continue
        if candidate:
            sentences.append(candidate)
        start = end

    tail = normalized[start:].strip()
    if tail:
        sentences.append(tail)

    return sentences
