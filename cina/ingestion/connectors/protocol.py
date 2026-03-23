"""Protocol definitions shared by ingestion source connectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from cina.models.document import Document


@dataclass(slots=True)
class RawDocument:
    """Raw source payload prior to domain parsing."""

    source_id: str
    payload: str
    metadata: dict[str, object]


@dataclass(slots=True)
class FetchConfig:
    """Connector enumeration options."""

    limit: int | None = None
    source_path: Path | None = None
    glob_pattern: str = "*"


class SourceConnector(Protocol):
    """Contract implemented by every source connector."""

    source_type: str

    def fetch_document_list(self, config: FetchConfig) -> AsyncIterator[RawDocument]:
        """Yield raw documents discovered at the configured source."""
        ...

    def parse(self, raw: RawDocument) -> Document:
        """Parse one raw payload into the normalized `Document` model."""
        ...
