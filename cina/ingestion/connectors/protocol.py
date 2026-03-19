from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from cina.models.document import Document


@dataclass(slots=True)
class RawDocument:
    source_id: str
    payload: str
    metadata: dict[str, object]


@dataclass(slots=True)
class FetchConfig:
    limit: int | None = None


class SourceConnector(Protocol):
    source_type: str

    async def fetch_document_list(self, config: FetchConfig) -> AsyncIterator[RawDocument]: ...

    def parse(self, raw: RawDocument) -> Document: ...
