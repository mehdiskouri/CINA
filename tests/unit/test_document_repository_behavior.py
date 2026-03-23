from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import date
from uuid import uuid4

import pytest

from cina.db.repositories.document import DocumentRepository
from cina.models.document import Document, Section


class _TransactionContext(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


class FakeConn:
    def __init__(self) -> None:
        self.fetchrow_result: dict[str, object] | None = None
        self.fetch_result: list[dict[str, object]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.fetchrow_calls.append((query, args))
        return self.fetchrow_result

    async def execute(self, query: str, *args: object) -> None:
        self.execute_calls.append((query, args))

    async def executemany(self, query: str, args_list: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, args_list))

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        _ = query
        _ = args
        return self.fetch_result

    def transaction(self) -> _TransactionContext:
        return _TransactionContext()


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


def _document_with_sections() -> tuple[Document, list[Section]]:
    document_id = uuid4()
    sections = [
        Section(
            id=uuid4(),
            document_id=document_id,
            section_type="abstract",
            heading="Abstract",
            content="content",
            order=1,
        )
    ]
    document = Document(
        id=document_id,
        source="pubmed",
        source_id="pmid-1",
        title="Study",
        authors=["A"],
        publication_date=date(2024, 1, 1),
        raw_metadata={"journal": "J"},
        sections=sections,
    )
    return document, sections


@pytest.mark.asyncio
async def test_upsert_document_returns_uuid() -> None:
    conn = FakeConn()
    doc, _ = _document_with_sections()
    conn.fetchrow_result = {"id": doc.id}
    repo = DocumentRepository(FakePool(conn))

    out = await repo.upsert_document(doc, ingestion_id=uuid4())

    assert out == doc.id


@pytest.mark.asyncio
async def test_upsert_document_raises_if_missing_return_row() -> None:
    conn = FakeConn()
    doc, _ = _document_with_sections()
    conn.fetchrow_result = None
    repo = DocumentRepository(FakePool(conn))

    with pytest.raises(RuntimeError, match="Failed to upsert document"):
        await repo.upsert_document(doc, ingestion_id=uuid4())


@pytest.mark.asyncio
async def test_replace_sections_handles_empty_and_non_empty() -> None:
    conn = FakeConn()
    doc, sections = _document_with_sections()
    repo = DocumentRepository(FakePool(conn))

    empty_count = await repo.replace_sections(doc.id, [])
    filled_count = await repo.replace_sections(doc.id, sections)

    assert empty_count == 0
    assert filled_count == 1
    assert conn.executemany_calls


@pytest.mark.asyncio
async def test_get_document_by_source_id_returns_dict_or_none() -> None:
    conn = FakeConn()
    repo = DocumentRepository(FakePool(conn))

    conn.fetchrow_result = None
    missing = await repo.get_document_by_source_id("pubmed", "missing")

    conn.fetchrow_result = {"id": str(uuid4()), "source": "pubmed", "source_id": "s", "title": "t"}
    found = await repo.get_document_by_source_id("pubmed", "s")

    assert missing is None
    assert found is not None
    assert found["source"] == "pubmed"
