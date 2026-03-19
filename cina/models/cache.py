from dataclasses import dataclass


@dataclass(slots=True)
class CachedResponse:
    tokens: list[str]
    citations: list[dict[str, object]]
    metadata: dict[str, object]
    metrics: dict[str, object]
    prompt_version: str
