from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from uuid import uuid4

import pytest

from cina.serving.search.bm25 import BM25Searcher, _metadata_to_dict


class FakeConn:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

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


class MappingLike(Mapping[str, object]):
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


@pytest.mark.asyncio
async def test_bm25_search_maps_rows_to_search_result() -> None:
    row_id = uuid4()
    conn = FakeConn(
        [
            {
                "id": row_id,
                "content": "HER2 positive breast cancer",
                "token_count": 5,
                "metadata": {"source": "pubmed"},
                "score": 0.91,
            }
        ]
    )
    searcher = BM25Searcher(FakePool(conn))

    results = await searcher.search("HER2", top_k=10)

    assert len(results) == 1
    assert results[0].chunk_id == row_id
    assert results[0].metadata == {"source": "pubmed"}
    assert results[0].score == 0.91
    assert conn.fetch_calls
    assert conn.fetch_calls[0][1] == ("HER2", 10)


def test_metadata_to_dict_handles_dict_mapping_and_json_text() -> None:
    assert _metadata_to_dict({"a": 1}) == {"a": 1}
    assert _metadata_to_dict(MappingLike({"b": "x"})) == {"b": "x"}
    assert _metadata_to_dict('{"c": 2}') == {"c": 2}


def test_metadata_to_dict_returns_empty_for_invalid_values() -> None:
    assert _metadata_to_dict("not-json") == {}
    assert _metadata_to_dict(123) == {}
