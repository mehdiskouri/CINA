from __future__ import annotations

from contextlib import AbstractAsyncContextManager

import pytest

from cina.db.repositories.query_log import QueryLogRepository


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> None:
        self.calls.append((query, args))


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
async def test_query_log_insert_executes_insert_statement() -> None:
    conn = FakeConn()
    repo = QueryLogRepository(FakePool(conn))

    await repo.insert(
        query_id="00000000-0000-0000-0000-000000000001",
        query_text="test query",
        prompt_version_id="v1.0",
        provider_used="anthropic",
        fallback_triggered=False,
        cache_hit=True,
        total_latency_ms=120,
        search_latency_ms=10,
        rerank_latency_ms=20,
        llm_latency_ms=80,
        chunks_retrieved=12,
        chunks_used=8,
        tenant_id="tenant-a",
    )

    assert len(conn.calls) == 1
    query, args = conn.calls[0]
    assert "INSERT INTO query_logs" in query
    assert len(args) == 13
    assert args[1] == "test query"
