from __future__ import annotations

import json

import pytest

from cina.models.cache import CachedResponse
from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.orchestration.cache.semantic_cache import SemanticCache, build_semantic_cache_middleware


class FakeHasher:
    def __init__(self, hash_value: str) -> None:
        self.hash_value = hash_value

    async def hash_embedding(self, _embedding: list[float]) -> str:
        return self.hash_value


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.scan_batches: list[tuple[int, list[str]]] = []
        self.deleted: list[tuple[str, ...]] = []

    async def get(self, key: str):
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        _ = ttl
        self.values[key] = value
        return True

    async def scan(self, cursor: int, match: str, count: int = 200):
        _ = (match, count)
        if self.scan_batches:
            return self.scan_batches.pop(0)
        return (0, [])

    async def delete(self, *keys: str) -> int:
        self.deleted.append(keys)
        for key in keys:
            self.values.pop(key, None)
        return len(keys)


@pytest.mark.asyncio
async def test_lookup_hits_best_similarity_and_handles_bytes_payload() -> None:
    redis = FakeRedis()
    cache = SemanticCache(redis, FakeHasher("h1"), similarity_threshold=0.8, ttl_seconds=60)

    payload = [
        {
            "embedding": [1.0, 0.0],
            "response": {
                "tokens": ["A"],
                "citations": [{"id": 1}],
                "metadata": {"provider": "x"},
                "metrics": {"m": 1.0},
                "prompt_version": "v1",
            },
        },
        {
            "embedding": [0.9, 0.1],
            "response": {
                "tokens": ["B"],
                "citations": [{"id": 2}],
                "metadata": {"provider": "y"},
                "metrics": {"m": 2.0},
                "prompt_version": "v1",
            },
        },
    ]
    redis.values["cina:cache:v1:h1"] = json.dumps(payload).encode("utf-8")

    out = await cache.lookup(embedding=[1.0, 0.0], prompt_version="v1")

    assert out is not None
    assert out.tokens in (["A"], ["B"])


@pytest.mark.asyncio
async def test_lookup_miss_for_absent_or_low_similarity_or_zero_norm() -> None:
    redis = FakeRedis()
    cache = SemanticCache(redis, FakeHasher("h2"), similarity_threshold=0.99, ttl_seconds=60)

    miss_absent = await cache.lookup(embedding=[0.0, 1.0], prompt_version="v1")
    assert miss_absent is None

    payload = [
        {
            "embedding": [0.0, 0.0],
            "response": {
                "tokens": ["Z"],
                "citations": [],
                "metadata": {},
                "metrics": {},
                "prompt_version": "v1",
            },
        },
    ]
    redis.values["cina:cache:v1:h2"] = json.dumps(payload)
    miss_zero_norm = await cache.lookup(embedding=[1.0, 0.0], prompt_version="v1")
    assert miss_zero_norm is None


@pytest.mark.asyncio
async def test_store_limits_entries_and_invalidate_scans_until_done() -> None:
    redis = FakeRedis()
    cache = SemanticCache(redis, FakeHasher("h3"), similarity_threshold=0.8, ttl_seconds=60)

    for idx in range(7):
        await cache.store(
            embedding=[1.0, 0.0],
            prompt_version="v2",
            response=CachedResponse(
                tokens=[str(idx)],
                citations=[],
                metadata={},
                metrics={},
                prompt_version="v2",
            ),
        )

    raw = redis.values["cina:cache:v2:h3"]
    loaded = json.loads(str(raw))
    assert len(loaded) == 5

    redis.values["cina:cache:v2:a"] = "x"
    redis.values["cina:cache:v2:b"] = "y"
    redis.scan_batches = [(1, ["cina:cache:v2:a"]), (0, ["cina:cache:v2:b"])]

    deleted = await cache.invalidate_version("v2")
    assert deleted == 2


@pytest.mark.asyncio
async def test_semantic_cache_middleware_hit_and_store_paths() -> None:
    redis = FakeRedis()
    cache = SemanticCache(redis, FakeHasher("h4"), similarity_threshold=0.8, ttl_seconds=60)
    middleware = build_semantic_cache_middleware(cache)

    await cache.store(
        embedding=[1.0, 0.0],
        prompt_version="v1",
        response=CachedResponse(
            tokens=["cached"],
            citations=[{"id": 1}],
            metadata={"provider": "openai"},
            metrics={"latency": 10.0},
            prompt_version="v1",
        ),
    )

    config_hit = CompletionConfig(metadata={"query_embedding": [1.0, 0.0], "prompt_version": "v1"})

    async def _next_unused(_messages: list[Message], _config: CompletionConfig):
        yield StreamChunk(text="live")

    hit_tokens = [
        c.text
        async for c in middleware([Message(role="user", content="q")], config_hit, _next_unused)
    ]
    assert hit_tokens == ["cached"]
    assert config_hit.metadata["cache_hit"] is True

    config_miss = CompletionConfig(
        metadata={
            "query_embedding": [0.0, 1.0],
            "prompt_version": "v1",
            "provider_used": "anthropic",
            "fallback_triggered": False,
            "citations": [{"id": 2}],
            "metrics_payload": {"llm_total_ms": 20.0, "ignored": "x"},
        },
    )

    async def _next_live(_messages: list[Message], _config: CompletionConfig):
        yield StreamChunk(text="live")

    miss_tokens = [
        c.text
        async for c in middleware([Message(role="user", content="q")], config_miss, _next_live)
    ]
    assert miss_tokens == ["live"]
    assert config_miss.metadata["cache_hit"] is False
