from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from fastapi import FastAPI

import cina.db.connection as conn_module
from cina.config.loader import clear_config_cache


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> None:
        self.executed.append((query, args))

    async def fetch(self, _query: str, *_args: object):
        return []


class _AcquireContext(AbstractAsyncContextManager[FakeConn]):
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()
        self.closed = False

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self.conn)

    async def close(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _write_config(path: Path) -> None:
    path.write_text(
        "serving:\n"
        "  rerank:\n"
        "    model: ''\n"
        "    device: cpu\n"
        "    top_n: 5\n"
        "orchestration:\n"
        "  providers:\n"
        "    primary:\n"
        "      name: anthropic\n"
        "      model: claude-sonnet-4-20250514\n"
        "      api_key_env: ANTHROPIC_API_KEY\n"
        "    fallback:\n"
        "      name: openai\n"
        "      model: gpt-4o\n"
        "      api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_lifespan_initializes_and_tears_down_app_state(tmp_path: Path) -> None:
    cfg = tmp_path / "lifespan.yaml"
    _write_config(cfg)

    original_config_path = os.environ.get("CINA_CONFIG_PATH")
    original_dsn = os.environ.get("DATABASE_URL")
    original_redis = os.environ.get("REDIS_URL")
    original_openai = os.environ.get("OPENAI_API_KEY")
    original_anthropic = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["CINA_CONFIG_PATH"] = str(cfg)
    os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    clear_config_cache()

    pool = FakePool()
    redis = FakeRedis()
    conn_module._pool = pool
    conn_module._redis = redis

    app = FastAPI()

    try:
        async with conn_module.lifespan(app):
            assert hasattr(app.state, "serving_pipeline")
            assert hasattr(app.state, "provider_router")
            assert hasattr(app.state, "semantic_cache")
            assert hasattr(app.state, "rate_limiter")
            assert hasattr(app.state, "cost_tracker")
            assert hasattr(app.state, "apikey_repo")
            assert app.state.redis is redis
    finally:
        if original_config_path is None:
            os.environ.pop("CINA_CONFIG_PATH", None)
        else:
            os.environ["CINA_CONFIG_PATH"] = original_config_path
        if original_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_dsn
        if original_redis is None:
            os.environ.pop("REDIS_URL", None)
        else:
            os.environ["REDIS_URL"] = original_redis
        if original_openai is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original_openai
        if original_anthropic is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = original_anthropic
        clear_config_cache()

    assert pool.closed is True
    assert redis.closed is True
    assert conn_module._pool is None
    assert conn_module._redis is None
    assert any("INSERT INTO prompt_versions" in query for query, _ in pool.conn.executed)
