from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cina.db.repositories.chunk import ChunkRepository, _metadata_to_dict
from cina.models.document import Chunk


class _TxContext(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


class FakeConn:
    def __init__(self) -> None:
        self.execute_results: list[str] = []
        self.fetch_rows: list[dict[str, object]] = []
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> _TxContext:
        return _TxContext()

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if self.execute_results:
            return self.execute_results.pop(0)
        return "UPDATE 1"

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        return self.fetch_rows


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


def _chunk(content_hash: str, idx: int = 0) -> Chunk:
    doc_id = uuid4()
    section_id = uuid4()
    return Chunk(
        id=uuid4(),
        section_id=section_id,
        document_id=doc_id,
        content=f"content-{content_hash}",
        content_hash=content_hash,
        token_count=10,
        chunk_index=idx,
        overlap_tokens=2,
        embedding_model="m",
        embedding_dim=4,
        metadata={
            "source": "pubmed",
            "created": datetime.now(UTC).isoformat(),
            "day": str(datetime.now(UTC).date()),
        },
    )


@pytest.mark.asyncio
async def test_bulk_upsert_counts_inserted_rows() -> None:
    conn = FakeConn()
    conn.execute_results = ["INSERT 0 1", "INSERT 0 0"]
    repo = ChunkRepository(FakePool(conn))

    inserted = await repo.bulk_upsert([_chunk("h1"), _chunk("h2", idx=1)])

    assert inserted == 1
    assert len(conn.executed) == 2


@pytest.mark.asyncio
async def test_update_embeddings_and_search_methods_map_results() -> None:
    conn = FakeConn()
    repo = ChunkRepository(FakePool(conn))

    await repo.update_embeddings(["id-1"], [[0.11, 0.22]], embedding_model="m", embedding_dim=2)

    row_id = uuid4()
    conn.fetch_rows = [
        {
            "id": row_id,
            "content": "chunk text",
            "token_count": 7,
            "metadata": '{"source":"fda"}',
            "score": 0.77,
        }
    ]
    vector = await repo.vector_search([0.1, 0.2], 3)
    bm25 = await repo.bm25_search("HER2", 4)

    assert len(vector) == 1
    assert vector[0].chunk_id == row_id
    assert vector[0].metadata == {"source": "fda"}
    assert len(bm25) == 1
    assert bm25[0].score == 0.77


@pytest.mark.asyncio
async def test_get_by_ids_and_unembedded_by_hashes() -> None:
    conn = FakeConn()
    repo = ChunkRepository(FakePool(conn))
    chunk_id = uuid4()

    conn.fetch_rows = [
        {
            "id": chunk_id,
            "section_id": uuid4(),
            "document_id": uuid4(),
            "content": "chunk",
            "content_hash": "h1",
            "token_count": 5,
            "chunk_index": 1,
            "overlap_tokens": 1,
            "embedding_model": "m",
            "embedding_dim": 4,
            "metadata": {"k": "v"},
        }
    ]
    got = await repo.get_by_ids([chunk_id])
    assert len(got) == 1

    conn.fetch_rows = [{"id": chunk_id, "content": "chunk", "content_hash": "h1"}]
    pending = await repo.get_unembedded_by_hashes(embedding_model="m", content_hashes=["h1"])
    assert pending == [{"id": str(chunk_id), "content": "chunk", "content_hash": "h1"}]

    assert await repo.get_unembedded_by_hashes(embedding_model="m", content_hashes=[]) == []


def test_chunk_metadata_to_dict_variants() -> None:
    assert _metadata_to_dict({"a": 1}) == {"a": 1}
    assert _metadata_to_dict('{"b":2}') == {"b": 2}
    assert _metadata_to_dict("bad") == {}
