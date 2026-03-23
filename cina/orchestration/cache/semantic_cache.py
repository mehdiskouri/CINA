"""Semantic cache using Redis + LSH bucketing + cosine similarity verification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from cina.models.cache import CachedResponse
from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.observability.metrics import cina_cache_requests_total

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

    from cina.orchestration.cache.lsh import LSHHasher
    from cina.orchestration.middleware import Handler, Middleware


@dataclass(slots=True)
class CacheEntry:
    """Single cached candidate tied to an embedding vector."""

    embedding: list[float]
    response: CachedResponse


class SemanticCache:
    """Semantic response cache with LSH prefilter and cosine verification."""

    def __init__(
        self,
        redis: Redis,
        hasher: LSHHasher,
        *,
        similarity_threshold: float,
        ttl_seconds: int,
    ) -> None:
        """Initialize cache backend and lookup/store thresholds."""
        self.redis = redis
        self.hasher = hasher
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(prompt_version: str, lsh_hash: str) -> str:
        return f"cina:cache:{prompt_version}:{lsh_hash}"

    async def lookup(
        self,
        *,
        embedding: list[float],
        prompt_version: str,
    ) -> CachedResponse | None:
        """Return best cached response if cosine threshold is met."""
        lsh_hash = await self.hasher.hash_embedding(embedding)
        payload = await self.redis.get(self._key(prompt_version, lsh_hash))
        if payload is None:
            cina_cache_requests_total.labels(result="miss").inc()
            return None

        raw_text = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        loaded = json.loads(raw_text)
        best: tuple[float, CachedResponse] | None = None
        for item in loaded:
            candidate = np.array(item["embedding"], dtype=np.float32)
            query = np.array(embedding, dtype=np.float32)
            denom = float(np.linalg.norm(candidate) * np.linalg.norm(query))
            if denom == 0:
                continue
            similarity = float(np.dot(candidate, query) / denom)
            if similarity < self.similarity_threshold:
                continue

            response = CachedResponse(
                tokens=list(item["response"]["tokens"]),
                citations=list(item["response"]["citations"]),
                metadata=dict(item["response"]["metadata"]),
                metrics=dict(item["response"]["metrics"]),
                prompt_version=str(item["response"]["prompt_version"]),
            )
            if best is None or similarity > best[0]:
                best = (similarity, response)

        if best is None:
            cina_cache_requests_total.labels(result="miss").inc()
            return None

        cina_cache_requests_total.labels(result="hit").inc()
        return best[1]

    async def store(
        self,
        *,
        embedding: list[float],
        prompt_version: str,
        response: CachedResponse,
    ) -> None:
        """Store a response candidate under prompt version and LSH bucket."""
        lsh_hash = await self.hasher.hash_embedding(embedding)
        key = self._key(prompt_version, lsh_hash)
        existing = await self.redis.get(key)
        entries: list[dict[str, object]] = []
        if existing:
            text = existing.decode("utf-8") if isinstance(existing, bytes) else str(existing)
            loaded = json.loads(text)
            if isinstance(loaded, list):
                entries = loaded

        entries.append(
            {
                "embedding": embedding,
                "response": {
                    "tokens": response.tokens,
                    "citations": response.citations,
                    "metadata": response.metadata,
                    "metrics": response.metrics,
                    "prompt_version": response.prompt_version,
                },
            },
        )
        entries = entries[-5:]
        await self.redis.setex(key, self.ttl_seconds, json.dumps(entries, ensure_ascii=True))

    async def invalidate_version(self, prompt_version: str) -> int:
        """Delete all cache keys associated with a prompt version."""
        pattern = f"cina:cache:{prompt_version}:*"
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                deleted += await self.redis.delete(*keys)
            if cursor == 0:
                break
        return deleted


def build_semantic_cache_middleware(cache: SemanticCache) -> Middleware:
    """Build middleware that serves/stores responses through semantic cache."""

    async def middleware(
        messages: list[Message],
        config: CompletionConfig,
        next_handler: Handler,
    ) -> AsyncIterator[StreamChunk]:
        embedding = config.metadata.get("query_embedding")
        prompt_version = str(config.metadata.get("prompt_version", "v1.0"))
        if isinstance(embedding, list):
            cached = await cache.lookup(embedding=embedding, prompt_version=prompt_version)
            if cached is not None:
                config.metadata["cache_hit"] = True
                config.metadata["cached_metrics"] = cached.metrics
                config.metadata["cached_citations"] = cached.citations
                for token in cached.tokens:
                    yield StreamChunk(text=token)
                return

        config.metadata["cache_hit"] = False
        streamed_tokens: list[str] = []
        async for chunk in next_handler(messages, config):
            streamed_tokens.append(chunk.text)
            yield chunk

        if isinstance(embedding, list) and streamed_tokens:
            raw_citations = config.metadata.get("citations", [])
            citations: list[dict[str, object]]
            if isinstance(raw_citations, list):
                citations = [item for item in raw_citations if isinstance(item, dict)]
            else:
                citations = []

            raw_metrics = config.metadata.get("metrics_payload", {})
            metrics: dict[str, object]
            if isinstance(raw_metrics, dict):
                metrics = {
                    str(key): float(value)
                    for key, value in raw_metrics.items()
                    if isinstance(value, (int, float))
                }
            else:
                metrics = {}

            await cache.store(
                embedding=embedding,
                prompt_version=prompt_version,
                response=CachedResponse(
                    tokens=streamed_tokens,
                    citations=citations,
                    metadata={
                        "provider": config.metadata.get("provider_used", "unknown"),
                        "fallback_triggered": bool(
                            config.metadata.get("fallback_triggered", False),
                        ),
                    },
                    metrics=metrics,
                    prompt_version=prompt_version,
                ),
            )

    return middleware
