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
        try:
            await _pool.close()
        except RuntimeError as exc:
            # Tests can leave a pool bound to a closed event loop between sessions.
            if "Event loop is closed" not in str(exc):
                raise
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
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await create_pool()

    # Initialise serving components and attach to app state
    cfg = load_config()
    scfg = cfg.serving
    pcfg = cfg.orchestration.providers.primary

    from cina.orchestration.providers.anthropic import AnthropicProvider
    from cina.serving.pipeline import ServingPipeline
    from cina.serving.rerank.cross_encoder import CrossEncoderReranker
    from cina.serving.search.embed import QueryEmbedder

    embedder = QueryEmbedder()

    reranker: CrossEncoderReranker | None = None
    try:
        reranker = CrossEncoderReranker(
            scfg.rerank.model,
            device=scfg.rerank.device,
            top_n=scfg.rerank.top_n,
        )
        reranker.warmup()
    except Exception:
        import structlog

        structlog.get_logger("cina.lifespan").warning(
            "cross_encoder_warmup_failed_serving_without_reranker"
        )

    provider = AnthropicProvider(
        model=pcfg.model,
        api_key_env=pcfg.api_key_env,
        timeout_connect=pcfg.timeout_connect,
        timeout_read=pcfg.timeout_read,
    )

    pipeline = ServingPipeline(
        pool,
        reranker=reranker,
        embedder=embedder,
        provider=provider,
    )

    app.state.serving_pipeline = pipeline
    app.state.reranker = reranker
    app.state.provider = provider

    try:
        yield
    finally:
        await close_pool()
