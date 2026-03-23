from __future__ import annotations

from contextlib import AbstractAsyncContextManager

import pytest

from cina.db.repositories.cost_event import CostEventRepository


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
async def test_cost_event_insert_executes_insert_statement() -> None:
    conn = FakeConn()
    repo = CostEventRepository(FakePool(conn))

    await repo.insert(
        query_id="00000000-0000-0000-0000-000000000002",
        tenant_id="tenant-b",
        provider="openai",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.012,
        cache_hit=False,
    )

    assert len(conn.calls) == 1
    query, args = conn.calls[0]
    assert "INSERT INTO cost_events" in query
    assert len(args) == 8
    assert args[2] == "openai"
