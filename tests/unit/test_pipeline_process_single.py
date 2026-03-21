from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import uuid4

import pytest

from cina.ingestion.connectors.protocol import RawDocument
from cina.ingestion.pipeline import _process_single_document
from cina.models.document import Chunk, Document, Section


@dataclass
class FakeConnector:
    def parse(self, raw: RawDocument) -> Document:
        doc_id = uuid4()
        section = Section(
            id=uuid4(),
            document_id=doc_id,
            section_type="abstract",
            heading="Abstract",
            content=raw.payload,
            order=0,
        )
        return Document(
            id=doc_id,
            source="pubmed",
            source_id=raw.source_id,
            title="Title",
            authors=["A"],
            publication_date=date(2024, 1, 1),
            sections=[section],
        )


class FakeDocumentRepo:
    def __init__(self) -> None:
        self.replaced_sections = 0

    async def upsert_document(self, document: Document, ingestion_id):  # type: ignore[no-untyped-def]
        return document.id

    async def replace_sections(self, document_id, sections):  # type: ignore[no-untyped-def]
        self.replaced_sections = len(sections)
        return self.replaced_sections


class FakeChunkRepo:
    def __init__(self) -> None:
        self.saved = 0
        self.last_chunks: list[Chunk] = []

    async def bulk_upsert(self, chunks: list[Chunk]) -> int:
        self.saved = len(chunks)
        self.last_chunks = chunks
        return self.saved

    async def get_unembedded_by_hashes(
        self,
        *,
        embedding_model: str,
        content_hashes: list[str],
    ) -> list[dict[str, object]]:
        return [
            {
                "id": str(chunk.id),
                "content": chunk.content,
                "content_hash": chunk.content_hash,
            }
            for chunk in self.last_chunks
            if chunk.content_hash in content_hashes and chunk.embedding_model == embedding_model
        ]


class FakeChunker:
    def chunk_document(self, document: Document, embedding_model: str) -> list[Chunk]:
        return [
            Chunk(
                id=uuid4(),
                section_id=document.sections[0].id,
                document_id=document.id,
                content="chunk-text",
                content_hash="hash-1",
                token_count=4,
                chunk_index=0,
                embedding_model=embedding_model,
                embedding_dim=512,
                metadata={"source": document.source},
            )
        ]


class FakeQueue:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def enqueue(self, message: dict[str, object], queue_name: str) -> str:
        self.messages.append(message)
        return "1-0"


@pytest.mark.asyncio
async def test_process_single_document_happy_path() -> None:
    raw = RawDocument(source_id="pmc1", payload="Text body", metadata={})
    document_repo = FakeDocumentRepo()
    chunk_repo = FakeChunkRepo()
    queue = FakeQueue()

    inserted, error = await _process_single_document(
        raw,
        connector=FakeConnector(),
        document_repo=document_repo,  # type: ignore[arg-type]
        chunk_repo=chunk_repo,  # type: ignore[arg-type]
        chunker=FakeChunker(),  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        queue_name="cina:queue:ingestion",
        ingestion_id=uuid4(),
        embedding_model="text-embedding-3-small",
        embedding_dim=512,
    )

    assert error is None
    assert inserted == 1
    assert document_repo.replaced_sections == 1
    assert chunk_repo.saved == 1
    assert len(queue.messages) == 1
    assert queue.messages[0]["embedding_model"] == "text-embedding-3-small"
