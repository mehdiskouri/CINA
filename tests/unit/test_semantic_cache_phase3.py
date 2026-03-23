from __future__ import annotations

import fnmatch
import json

import pytest

from cina.models.cache import CachedResponse
from cina.orchestration.cache.lsh import LSHHasher
from cina.orchestration.cache.semantic_cache import SemanticCache


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: object):
        self.values[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: object):
        _ = ttl
        self.values[key] = value
        return True

    async def scan(self, cursor: int, match: str, count: int = 100):
        keys = [k for k in self.values if fnmatch.fnmatch(k, match)]
        return 0, keys[:count]

    async def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            if key in self.values:
                deleted += 1
            self.values.pop(key, None)
        return deleted


@pytest.mark.asyncio
async def test_semantic_cache_store_hit_miss_and_invalidate() -> None:
    redis = FakeRedis()
    hasher = LSHHasher(redis, num_hyperplanes=16, dimensions=4, seed=5)
    cache = SemanticCache(redis, hasher, similarity_threshold=0.9, ttl_seconds=60)

    embedding = [0.1, 0.2, 0.3, 0.4]
    response = CachedResponse(
        tokens=["hello", " world"],
        citations=[{"id": 1}],
        metadata={"provider": "anthropic"},
        metrics={"llm_total_ms": 10.0},
        prompt_version="v1.0",
    )

    miss = await cache.lookup(embedding=[-0.1, -0.2, -0.3, -0.4], prompt_version="v1.0")
    assert miss is None

    await cache.store(embedding=embedding, prompt_version="v1.0", response=response)
    hit = await cache.lookup(embedding=embedding, prompt_version="v1.0")

    assert hit is not None
    assert "".join(hit.tokens) == "hello world"
    assert hit.prompt_version == "v1.0"

    deleted = await cache.invalidate_version("v1.0")
    assert deleted >= 1


@pytest.mark.asyncio
async def test_semantic_cache_similarity_gate() -> None:
    redis = FakeRedis()
    hasher = LSHHasher(redis, num_hyperplanes=16, dimensions=4, seed=7)
    cache = SemanticCache(redis, hasher, similarity_threshold=0.98, ttl_seconds=60)

    key_hash = await hasher.hash_embedding([1.0, 0.0, 0.0, 0.0])
    key = f"cina:cache:v1.0:{key_hash}"
    redis.values[key] = json.dumps(
        [
            {
                "embedding": [1.0, 0.0, 0.0, 0.0],
                "response": {
                    "tokens": ["A"],
                    "citations": [],
                    "metadata": {},
                    "metrics": {},
                    "prompt_version": "v1.0",
                },
            },
        ],
    )

    # Similarity is low enough to fail 0.98 threshold.
    out = await cache.lookup(embedding=[0.0, 1.0, 0.0, 0.0], prompt_version="v1.0")
    assert out is None
