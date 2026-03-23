from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from uuid import uuid4

import pytest

from cina.db.repositories.apikey import APIKeyRepository


class FakeConn:
    def __init__(self) -> None:
        self.fetchrow_result: dict[str, object] | None = None
        self.execute_result = "UPDATE 0"
        self.fetch_result: list[dict[str, object]] = []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.fetch_calls.append((query, args))
        return self.fetchrow_result

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return self.execute_result

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((query, args))
        return self.fetch_result


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
async def test_create_key_returns_uuid() -> None:
    conn = FakeConn()
    key_id = uuid4()
    conn.fetchrow_result = {"id": key_id}
    repo = APIKeyRepository(FakePool(conn))

    out = await repo.create_key(key_hash="hash", tenant_id="tenant-a", name="default")

    assert out == key_id


@pytest.mark.asyncio
async def test_create_key_raises_when_insert_returns_none() -> None:
    conn = FakeConn()
    conn.fetchrow_result = None
    repo = APIKeyRepository(FakePool(conn))

    with pytest.raises(RuntimeError, match="Failed to create API key"):
        await repo.create_key(key_hash="hash", tenant_id="tenant-a", name="default")


@pytest.mark.asyncio
async def test_revoke_key_interprets_row_count() -> None:
    conn = FakeConn()
    repo = APIKeyRepository(FakePool(conn))

    conn.execute_result = "UPDATE 1"
    assert await repo.revoke_key("00000000-0000-0000-0000-000000000001") is True

    conn.execute_result = "UPDATE 0"
    assert await repo.revoke_key("00000000-0000-0000-0000-000000000001") is False


@pytest.mark.asyncio
async def test_list_keys_with_and_without_tenant_filter() -> None:
    conn = FakeConn()
    conn.fetch_result = [{"id": str(uuid4()), "tenant_id": "t", "name": "n", "active": True}]
    repo = APIKeyRepository(FakePool(conn))

    no_filter = await repo.list_keys()
    with_filter = await repo.list_keys("tenant-a")

    assert len(no_filter) == 1
    assert len(with_filter) == 1
    assert conn.fetch_calls[0][1] == ()
    assert conn.fetch_calls[1][1] == ("tenant-a",)


@pytest.mark.asyncio
async def test_validate_token_returns_record_on_hash_match(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn()
    record_id = uuid4()
    conn.fetch_result = [
        {
            "id": record_id,
            "key_hash": "$2b$12$dummyhash",
            "tenant_id": "tenant-z",
            "name": "primary",
        }
    ]
    repo = APIKeyRepository(FakePool(conn))
    monkeypatch.setattr("cina.db.repositories.apikey.bcrypt.checkpw", lambda _token, _hash: True)

    record = await repo.validate_token("cina_sk_token")

    assert record is not None
    assert record.id == record_id
    assert record.tenant_id == "tenant-z"
