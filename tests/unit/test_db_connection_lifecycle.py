from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace

import pytest

import cina.db.connection as conn_module


class FakeConn:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def execute(self, _query: str) -> None:
        if self.fail:
            raise RuntimeError("db down")


class _AcquireContext(AbstractAsyncContextManager[FakeConn]):
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


class FakePool:
    def __init__(self, *, fail: bool = False) -> None:
        self.conn = FakeConn(fail=fail)
        self.closed = False

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self.conn)

    async def close(self) -> None:
        self.closed = True


class FakeRedisClient:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_create_pool_raises_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    conn_module._pool = None
    monkeypatch.setattr(
        conn_module,
        "load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(
                postgres=SimpleNamespace(dsn_env="DATABASE_URL", pool_min=1, pool_max=2),
                redis=SimpleNamespace(url_env="REDIS_URL", pool_max=10),
            ),
        ),
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="Missing database DSN"):
        await conn_module.create_pool()


@pytest.mark.asyncio
async def test_create_pool_and_get_pool_reuse_cached_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    conn_module._pool = None
    fake_pool = FakePool()
    monkeypatch.setattr(
        conn_module,
        "load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(
                postgres=SimpleNamespace(dsn_env="DATABASE_URL", pool_min=1, pool_max=2),
                redis=SimpleNamespace(url_env="REDIS_URL", pool_max=10),
            ),
        ),
    )
    monkeypatch.setenv("DATABASE_URL", "postgres://x")

    async def _create_pool(**_kwargs):
        return fake_pool

    monkeypatch.setattr(conn_module.asyncpg, "create_pool", _create_pool)

    first = await conn_module.create_pool()
    second = await conn_module.get_pool()

    assert first is fake_pool
    assert second is fake_pool


@pytest.mark.asyncio
async def test_create_redis_requires_url_and_can_close(monkeypatch: pytest.MonkeyPatch) -> None:
    conn_module._redis = None
    fake_redis = FakeRedisClient()
    monkeypatch.setattr(
        conn_module,
        "load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(
                postgres=SimpleNamespace(dsn_env="DATABASE_URL", pool_min=1, pool_max=2),
                redis=SimpleNamespace(url_env="REDIS_URL", pool_max=10),
            ),
        ),
    )
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="Missing Redis URL"):
        await conn_module.create_redis()

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(conn_module.Redis, "from_url", lambda _url, max_connections: fake_redis)

    created = await conn_module.create_redis()
    assert created is fake_redis

    await conn_module.close_redis()
    assert fake_redis.closed is True


@pytest.mark.asyncio
async def test_close_pool_handles_closed_event_loop_runtime_error() -> None:
    class BrokenPool:
        async def close(self) -> None:
            raise RuntimeError("Event loop is closed")

    conn_module._pool = BrokenPool()
    await conn_module.close_pool()
    assert conn_module._pool is None


@pytest.mark.asyncio
async def test_db_healthcheck_ok_and_error_paths() -> None:
    conn_module._pool = FakePool(fail=False)
    ok = await conn_module.db_healthcheck()
    assert ok["status"] == "ok"

    conn_module._pool = FakePool(fail=True)
    err = await conn_module.db_healthcheck()
    assert err["status"] == "error"
