"""Models for semantic-cache payloads."""

from dataclasses import dataclass


@dataclass(slots=True)
class CachedResponse:
    """Cached query response tokens, citations, and metrics."""

    tokens: list[str]
    citations: list[dict[str, object]]
    metadata: dict[str, object]
    metrics: dict[str, object]
    prompt_version: str
