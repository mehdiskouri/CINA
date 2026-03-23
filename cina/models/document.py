"""Core ingestion and retrieval document domain models."""

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID


@dataclass(slots=True)
class Section:
    """Atomic section extracted from a source document."""

    id: UUID
    document_id: UUID
    section_type: str
    heading: str | None
    content: str
    order: int
    created_at: datetime | None = None

    def __hash__(self) -> int:
        """Provide stable identity for deduplication in sets/maps."""
        return hash((self.document_id, self.section_type, self.order, self.content))


@dataclass(slots=True)
class Document:
    """Normalized source document with optional section list."""

    id: UUID
    source: str
    source_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    publication_date: date | None = None
    raw_metadata: dict[str, object] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)

    def __hash__(self) -> int:
        """Hash by source identity to avoid duplicate logical documents."""
        return hash((self.source, self.source_id))


@dataclass(slots=True)
class Chunk:
    """Retrieval chunk with embedding and metadata."""

    id: UUID
    section_id: UUID
    document_id: UUID
    content: str
    content_hash: str
    token_count: int
    chunk_index: int
    overlap_tokens: int = 0
    embedding: list[float] | None = None
    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 512
    metadata: dict[str, object] = field(default_factory=dict)

    def __hash__(self) -> int:
        """Hash by content fingerprint and embedding model variant."""
        return hash((self.content_hash, self.embedding_model))
