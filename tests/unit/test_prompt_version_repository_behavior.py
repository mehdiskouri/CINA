from __future__ import annotations

from contextlib import AbstractAsyncContextManager

import pytest

from cina.db.repositories.prompt_version import PromptVersionRepository


class FakeConn:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object):
        self.executed.append((query, args))
        return self.rows

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
async def test_list_active_maps_prompt_versions() -> None:
    conn = FakeConn()
    conn.rows = [
        {
            "id": "v1",
            "system_prompt": "You are clinical",
            "description": "default",
            "traffic_weight": 1.0,
            "active": True,
        }
    ]
    repo = PromptVersionRepository(FakePool(conn))

    rows = await repo.list_active()

    assert len(rows) == 1
    assert rows[0].id == "v1"
    assert rows[0].active is True


@pytest.mark.asyncio
async def test_upsert_executes_insert_statement() -> None:
    conn = FakeConn()
    repo = PromptVersionRepository(FakePool(conn))

    await repo.upsert(
        version_id="v2",
        system_prompt="prompt",
        description=None,
        traffic_weight=0.5,
        active=False,
    )

    assert len(conn.executed) == 1
    assert "INSERT INTO prompt_versions" in conn.executed[0][0]
