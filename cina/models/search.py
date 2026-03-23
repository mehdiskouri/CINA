"""Models for retrieval search outputs."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class SearchResult:
    """Single ranked retrieval hit used by serving stages."""

    chunk_id: UUID
    content: str
    token_count: int
    metadata: dict[str, object]
    score: float
