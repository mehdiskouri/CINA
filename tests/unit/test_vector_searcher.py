from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from uuid import uuid4

import pytest

from cina.serving.search.vector import VectorSearcher, _metadata_to_dict


class FakeConn:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.execute_calls: list[str] = []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, statement: str) -> None:
        self.execute_calls.append(statement)

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((query, args))
        return self.rows


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
async def test_vector_search_sets_ef_search_and_maps_results() -> None:
    row_id = uuid4()
    conn = FakeConn(
        [
            {
                "id": row_id,
                "content": "trastuzumab study",
                "token_count": 3,
                "metadata": '{"source":"fda"}',
                "score": 0.88,
            },
        ],
    )
    searcher = VectorSearcher(FakePool(conn), ef_search=77)

    results = await searcher.search([0.1, 0.2, 0.3], top_k=5)

    assert conn.execute_calls == ["SET LOCAL hnsw.ef_search = 77"]
    assert conn.fetch_calls
    vector_arg, top_k_arg = conn.fetch_calls[0][1]
    assert isinstance(vector_arg, str)
    assert top_k_arg == 5
    assert len(results) == 1
    assert results[0].chunk_id == row_id
    assert results[0].metadata == {"source": "fda"}


def test_vector_metadata_to_dict_handles_invalid_json() -> None:
    assert _metadata_to_dict('{"source": "pubmed"}') == {"source": "pubmed"}
    assert _metadata_to_dict("bad-json") == {}
    assert _metadata_to_dict(None) == {}
