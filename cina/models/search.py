from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class SearchResult:
    chunk_id: UUID
    content: str
    token_count: int
    metadata: dict[str, object]
    score: float
