from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI
from redis.asyncio import Redis

from cina.config import load_config

_pool: asyncpg.Pool | None = None
_redis: Redis | None = None


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


async def create_redis() -> Redis:
    global _redis
    cfg = load_config()
    redis_url = __import__("os").getenv(cfg.database.redis.url_env)
    if not redis_url:
        raise RuntimeError(f"Missing Redis URL env var: {cfg.database.redis.url_env}")
    if _redis is None:
        _redis = Redis.from_url(redis_url, max_connections=cfg.database.redis.pool_max)
    return _redis


async def get_redis() -> Redis:
    if _redis is None:
        return await create_redis()
    return _redis


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


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


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
    redis = await create_redis()

    # Initialise serving components and attach to app state
    cfg = load_config()
    scfg = cfg.serving
    ocfg = cfg.orchestration

    from cina.db.repositories.apikey import APIKeyRepository
    from cina.db.repositories.cost_event import CostEventRepository
    from cina.db.repositories.prompt_version import PromptVersionRepository
    from cina.db.repositories.query_log import QueryLogRepository
    from cina.models.provider import CompletionConfig, Message, StreamChunk
    from cina.orchestration.cache.lsh import LSHHasher
    from cina.orchestration.cache.semantic_cache import (
        SemanticCache,
        build_semantic_cache_middleware,
    )
    from cina.orchestration.limits.cost_tracker import CostTracker, build_cost_tracking_middleware
    from cina.orchestration.limits.rate_limiter import RateLimiter
    from cina.orchestration.middleware import compose
    from cina.orchestration.providers.anthropic import AnthropicProvider
    from cina.orchestration.providers.openai import OpenAIProvider
    from cina.orchestration.providers.protocol import LLMProviderProtocol
    from cina.orchestration.routing.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
    from cina.orchestration.routing.fallback import ConcurrentFallbackExecutor
    from cina.orchestration.routing.prompt_router import PromptRouter
    from cina.orchestration.routing.provider_router import ProviderRouter
    from cina.serving.context.prompt import CLINICAL_SYSTEM_PROMPT
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

    primary_cfg = ocfg.providers.primary
    fallback_cfg = ocfg.providers.fallback

    primary_provider = AnthropicProvider(
        model=primary_cfg.model,
        api_key_env=primary_cfg.api_key_env,
        timeout_connect=primary_cfg.timeout_connect,
        timeout_read=primary_cfg.timeout_read,
    )
    fallback_provider = OpenAIProvider(
        model=fallback_cfg.model,
        api_key_env=fallback_cfg.api_key_env,
        timeout_connect=fallback_cfg.timeout_connect,
        timeout_read=fallback_cfg.timeout_read,
    )

    breaker = CircuitBreaker(
        redis,
        CircuitBreakerConfig(
            max_failures=ocfg.fallback.circuit_breaker_failures,
            cooldown_seconds=ocfg.fallback.circuit_breaker_cooldown,
        ),
    )
    router = ProviderRouter(
        primary_name=primary_cfg.name,
        primary=primary_provider,
        fallback_name=fallback_cfg.name,
        fallback=fallback_provider,
        breaker=breaker,
    )
    fallback_executor = ConcurrentFallbackExecutor(router, ocfg.fallback.ttft_threshold_seconds)

    hasher = LSHHasher(
        redis,
        num_hyperplanes=ocfg.cache.num_hyperplanes,
        dimensions=cfg.ingestion.embedding.dimensions,
    )
    semantic_cache = SemanticCache(
        redis,
        hasher,
        similarity_threshold=ocfg.cache.similarity_threshold,
        ttl_seconds=ocfg.cache.ttl_seconds,
    )

    cost_repo = CostEventRepository(pool)
    cost_tracker = CostTracker(cost_repo)

    async def provider_handler(
        messages: list[Message], completion_config: CompletionConfig
    ) -> AsyncIterator[StreamChunk]:
        result = await fallback_executor.complete(messages, completion_config)
        completion_config.metadata["provider_used"] = result.provider_name
        completion_config.metadata["fallback_triggered"] = result.fallback_triggered
        provider_obj: LLMProviderProtocol
        if result.provider_name == primary_cfg.name:
            provider_obj = primary_provider
        else:
            provider_obj = fallback_provider
        completion_config.metadata["provider_model"] = provider_obj.model
        completion_config.metadata["estimate_cost"] = provider_obj.estimate_cost
        async for chunk in result.stream:
            yield chunk

    orchestrated_handler = compose(
        build_cost_tracking_middleware(cost_tracker),
        build_semantic_cache_middleware(semantic_cache),
    )(provider_handler)

    prompt_repo = PromptVersionRepository(pool)
    await prompt_repo.upsert(
        version_id=ocfg.prompt.default_version,
        system_prompt=CLINICAL_SYSTEM_PROMPT,
        description="Default clinical system prompt",
        traffic_weight=1.0,
        active=True,
    )
    prompt_router = PromptRouter(prompt_repo, default_version=ocfg.prompt.default_version)
    query_log_repo = QueryLogRepository(pool)
    rate_limiter = RateLimiter(redis, requests_per_minute=ocfg.rate_limit.requests_per_minute)
    apikey_repo = APIKeyRepository(pool)

    pipeline = ServingPipeline(
        pool,
        reranker=reranker,
        embedder=embedder,
        provider=primary_provider,
        handler=orchestrated_handler,
        prompt_router=prompt_router,
        query_log_repo=query_log_repo,
        cost_tracker=cost_tracker,
    )

    app.state.serving_pipeline = pipeline
    app.state.reranker = reranker
    app.state.provider = primary_provider
    app.state.provider_router = router
    app.state.semantic_cache = semantic_cache
    app.state.rate_limiter = rate_limiter
    app.state.cost_tracker = cost_tracker
    app.state.apikey_repo = apikey_repo
    app.state.redis = redis

    try:
        yield
    finally:
        await close_pool()
        await close_redis()
