from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI

from cina.config import load_config

_pool: asyncpg.Pool | None = None


async def create_pool(dsn: str | None = None) -> asyncpg.Pool:
    global _pool
    cfg = load_config()
    database_url = dsn or __import__("os").getenv(cfg.database.postgres.dsn_env)
    if not database_url:
        raise RuntimeError(f"Missing database DSN env var: {cfg.database.postgres.dsn_env}")
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=database_url,
            min_size=cfg.database.postgres.pool_min,
            max_size=cfg.database.postgres.pool_max,
        )
    return _pool


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        return await create_pool()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def db_healthcheck() -> dict[str, Any]:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as exc:  # pragma: no cover - defensive check
        return {"status": "error", "error": str(exc)}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await create_pool()
    try:
        yield
    finally:
        await close_pool()
