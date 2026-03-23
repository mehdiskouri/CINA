"""Database and runtime resource lifecycle wiring for the API application."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import asyncpg
import structlog
from redis.asyncio import Redis

from cina.config import load_config
from cina.db.repositories.apikey import APIKeyRepository
from cina.db.repositories.cost_event import CostEventRepository
from cina.db.repositories.prompt_version import PromptVersionRepository
from cina.db.repositories.query_log import QueryLogRepository
from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.orchestration.cache.lsh import LSHHasher
from cina.orchestration.cache.semantic_cache import SemanticCache, build_semantic_cache_middleware
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
from cina.serving.pipeline import ServingPipeline, ServingPipelineDependencies
from cina.serving.rerank.cross_encoder import CrossEncoderReranker
from cina.serving.search.embed import QueryEmbedder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from cina.config.schema import AppConfig, ServingConfigModel
    from cina.models.provider import CompletionConfig, Message, StreamChunk
    from cina.orchestration.providers.protocol import LLMProviderProtocol


@dataclass(slots=True)
class _ConnectionState:
    """In-process shared connection handles."""

    pool: asyncpg.Pool | None = None
    redis: Redis | None = None


_state = _ConnectionState()

# Backward-compatible globals still referenced in tests and legacy code paths.
_pool: asyncpg.Pool | None = None
_redis: Redis | None = None


@dataclass(slots=True)
class _RuntimeComponents:
    """Typed bundle of initialized runtime components for app state wiring."""

    pipeline: ServingPipeline
    cost_tracker: CostTracker
    semantic_cache: SemanticCache
    prompt_router: PromptRouter
    query_log_repo: QueryLogRepository
    rate_limiter: RateLimiter
    apikey_repo: APIKeyRepository
    provider_router: ProviderRouter


async def create_pool(dsn: str | None = None) -> asyncpg.Pool:
    """Create or return the shared Postgres connection pool."""
    global _pool  # noqa: PLW0603
    cfg = load_config()
    database_url = dsn or os.getenv(cfg.database.postgres.dsn_env)
    if not database_url:
        message = f"Missing database DSN env var: {cfg.database.postgres.dsn_env}"
        raise RuntimeError(message)
    if _state.pool is None and _pool is not None:
        _state.pool = _pool
    if _state.pool is None:
        _state.pool = await asyncpg.create_pool(
            dsn=database_url,
            min_size=cfg.database.postgres.pool_min,
            max_size=cfg.database.postgres.pool_max,
        )
    _pool = _state.pool
    return _state.pool


async def get_pool() -> asyncpg.Pool:
    """Get the active Postgres connection pool."""
    global _pool  # noqa: PLW0602
    if _pool is not None:
        _state.pool = _pool
    if _state.pool is None:
        return await create_pool()
    return _state.pool


async def create_redis() -> Redis:
    """Create or return the shared Redis client."""
    global _redis  # noqa: PLW0603
    cfg = load_config()
    redis_url = os.getenv(cfg.database.redis.url_env)
    if not redis_url:
        message = f"Missing Redis URL env var: {cfg.database.redis.url_env}"
        raise RuntimeError(message)
    if _state.redis is None and _redis is not None:
        _state.redis = _redis
    if _state.redis is None:
        _state.redis = Redis.from_url(redis_url, max_connections=cfg.database.redis.pool_max)
    _redis = _state.redis
    return _state.redis


async def get_redis() -> Redis:
    """Get the active Redis client."""
    global _redis  # noqa: PLW0602
    if _state.redis is None and _redis is not None:
        _state.redis = _redis
    if _state.redis is None:
        return await create_redis()
    return _state.redis


async def close_pool() -> None:
    """Close and clear the shared Postgres pool."""
    global _pool  # noqa: PLW0603
    if _state.pool is None and _pool is not None:
        _state.pool = _pool
    if _state.pool is not None:
        try:
            await _state.pool.close()
        except RuntimeError as exc:
            # Tests can leave a pool bound to a closed event loop between sessions.
            if "Event loop is closed" not in str(exc):
                raise
        _state.pool = None
        _pool = None


async def close_redis() -> None:
    """Close and clear the shared Redis client."""
    global _redis  # noqa: PLW0603
    if _state.redis is None and _redis is not None:
        _state.redis = _redis
    if _state.redis is not None:
        await _state.redis.aclose()
        _state.redis = None
        _redis = None


async def db_healthcheck() -> dict[str, Any]:
    """Run a minimal DB probe query and return health payload."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    except (ConnectionError, OSError, RuntimeError, ValueError, asyncpg.PostgresError) as exc:
        return {"status": "error", "error": str(exc)}
    else:
        return {"status": "ok"}


async def _build_orchestrated_handler(
    *,
    cfg: AppConfig,
    redis: Redis,
    pool: asyncpg.Pool,
) -> _RuntimeComponents:
    scfg = cfg.serving
    ocfg = cfg.orchestration

    embedder = QueryEmbedder()
    reranker = _build_optional_reranker(scfg)

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
        messages: list[Message],
        completion_config: CompletionConfig,
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
        dependencies=ServingPipelineDependencies(
            reranker=reranker,
            embedder=embedder,
            provider=primary_provider,
            handler=orchestrated_handler,
            prompt_router=prompt_router,
            query_log_repo=query_log_repo,
            cost_tracker=cost_tracker,
        ),
    )

    return _RuntimeComponents(
        pipeline=pipeline,
        cost_tracker=cost_tracker,
        semantic_cache=semantic_cache,
        prompt_router=prompt_router,
        query_log_repo=query_log_repo,
        rate_limiter=rate_limiter,
        apikey_repo=apikey_repo,
        provider_router=router,
    )


def _build_optional_reranker(scfg: ServingConfigModel) -> CrossEncoderReranker | None:
    """Build and warm optional reranker, tolerating warmup failures."""
    reranker: CrossEncoderReranker | None = None
    try:
        reranker = CrossEncoderReranker(
            scfg.rerank.model,
            device=scfg.rerank.device,
            top_n=scfg.rerank.top_n,
        )
        reranker.warmup()
    except (RuntimeError, ValueError, OSError):
        structlog.get_logger("cina.lifespan").warning(
            "cross_encoder_warmup_failed_serving_without_reranker",
        )
    return reranker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared app runtime resources and tear them down on shutdown."""
    pool = await create_pool()
    redis = await create_redis()
    cfg = load_config()
    components = await _build_orchestrated_handler(cfg=cfg, redis=redis, pool=pool)

    app.state.serving_pipeline = components.pipeline
    app.state.reranker = components.pipeline.reranker
    app.state.provider = components.pipeline.provider
    app.state.provider_router = components.provider_router
    app.state.semantic_cache = components.semantic_cache
    app.state.rate_limiter = components.rate_limiter
    app.state.cost_tracker = components.cost_tracker
    app.state.apikey_repo = components.apikey_repo
    app.state.redis = redis

    try:
        yield
    finally:
        await close_pool()
        await close_redis()
