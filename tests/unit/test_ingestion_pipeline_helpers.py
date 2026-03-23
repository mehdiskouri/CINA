from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cina.ingestion.connectors.protocol import RawDocument
from cina.ingestion.pipeline import (
    _create_ingestion_job,
    _finalize_ingestion_job,
    _process_single_document,
    _update_job_progress,
)
from cina.models.document import Document, Section


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[dict[str, object], str]] = []

    async def enqueue(self, message: dict[str, object], queue_name: str) -> str:
        self.enqueued.append((message, queue_name))
        return "1-0"


class FakeConnector:
    def __init__(self, document: Document | None = None, fail: bool = False) -> None:
        self.document = document
        self.fail = fail

    def parse(self, _raw: RawDocument) -> Document:
        if self.fail or self.document is None:
            raise RuntimeError("parse failed")
        return self.document


class FakeDocumentRepo:
    def __init__(self, doc_id) -> None:
        self.doc_id = doc_id
        self.replaced: list[tuple[object, list[Section]]] = []

    async def upsert_document(self, _doc: Document, ingestion_id):
        _ = ingestion_id
        return self.doc_id

    async def replace_sections(self, document_id, sections: list[Section]) -> None:
        self.replaced.append((document_id, sections))


class FakeChunker:
    def __init__(self, chunks) -> None:
        self.chunks = chunks

    def chunk_document(self, _doc: Document, embedding_model: str):
        _ = embedding_model
        return self.chunks


class FakeChunkRepo:
    def __init__(self, pending: list[dict[str, object]]) -> None:
        self.pending = pending

    async def bulk_upsert(self, chunks) -> int:
        return len(chunks)

    async def get_unembedded_by_hashes(self, *, embedding_model: str, content_hashes: list[str]):
        _ = (embedding_model, content_hashes)
        return self.pending


class FakeConn:
    def __init__(self, fetchrow_result=None) -> None:
        self.fetchrow_result = fetchrow_result
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object):
        self.executed.append((query, args))
        return self.fetchrow_result

    async def execute(self, query: str, *args: object) -> None:
        self.executed.append((query, args))


class _AcquireContext(AbstractAsyncContextManager[FakeConn]):
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self.conn)


@pytest.mark.asyncio
async def test_process_single_document_success_enqueues_pending_embeddings() -> None:
    doc_id = uuid4()
    section = Section(
        id=uuid4(),
        document_id=uuid4(),
        section_type="abstract",
        heading="h",
        content="c",
        order=0,
    )
    document = Document(
        id=uuid4(),
        source="pubmed",
        source_id="pm-1",
        title="Study",
        authors=["A"],
        publication_date=datetime.now(UTC).date(),
        raw_metadata={"k": "v"},
        sections=[section],
    )
    raw = RawDocument(source_id="pm-1", payload="{}", metadata={})

    fake_chunk = type("ChunkLike", (), {"content_hash": "h1"})()
    queue = FakeQueue()

    created, error = await _process_single_document(
        raw,
        connector=FakeConnector(document=document),
        document_repo=FakeDocumentRepo(doc_id),
        chunk_repo=FakeChunkRepo(pending=[{"id": "c1", "content": "txt", "content_hash": "h1"}]),
        chunker=FakeChunker([fake_chunk]),
        queue=queue,
        queue_name="ingestion-q",
        ingestion_id=uuid4(),
        embedding_model="m",
        embedding_dim=4,
    )

    assert error is None
    assert created == 1
    assert len(queue.enqueued) == 1
    payload, qname = queue.enqueued[0]
    assert qname == "ingestion-q"
    assert payload["chunk_id"] == "c1"


@pytest.mark.asyncio
async def test_process_single_document_handles_parse_failure() -> None:
    created, error = await _process_single_document(
        RawDocument(source_id="bad", payload="{}", metadata={}),
        connector=FakeConnector(fail=True),
        document_repo=FakeDocumentRepo(uuid4()),
        chunk_repo=FakeChunkRepo([]),
        chunker=FakeChunker([]),
        queue=FakeQueue(),
        queue_name="q",
        ingestion_id=uuid4(),
        embedding_model="m",
        embedding_dim=4,
    )

    assert created == 0
    assert error is not None
    assert error.startswith("bad:")


@pytest.mark.asyncio
async def test_ingestion_job_helpers_create_update_finalize() -> None:
    job_id = uuid4()
    conn = FakeConn(fetchrow_result={"id": str(job_id)})
    pool = FakePool(conn)

    created = await _create_ingestion_job(pool, "pubmed")
    await _update_job_progress(pool, created, 3, 10)
    await _finalize_ingestion_job(
        pool,
        created,
        documents_processed=3,
        chunks_created=10,
        errors=[],
    )

    assert created == job_id
    assert len(conn.executed) == 3


@pytest.mark.asyncio
async def test_create_ingestion_job_raises_when_insert_returns_none() -> None:
    pool = FakePool(FakeConn(fetchrow_result=None))

    with pytest.raises(RuntimeError, match="Failed to create ingestion job"):
        await _create_ingestion_job(pool, "pubmed")
