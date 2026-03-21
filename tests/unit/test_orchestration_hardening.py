from __future__ import annotations

import fnmatch
import time

import pytest

from cina.db.repositories.prompt_version import PromptVersion
from cina.orchestration.cache.lsh import LSHHasher
from cina.orchestration.limits.rate_limiter import RateLimiter
from cina.orchestration.routing.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from cina.orchestration.routing.prompt_router import PromptRouter


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def delete(self, *keys: str) -> FakePipeline:
        self.ops.append(("delete", keys, {}))
        return self

    def set(self, key: str, value: object) -> FakePipeline:
        self.ops.append(("set", (key, value), {}))
        return self

    def setex(self, key: str, ttl: int, value: object) -> FakePipeline:
        self.ops.append(("setex", (key, ttl, value), {}))
        return self

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> FakePipeline:
        self.ops.append(("zremrangebyscore", (key, min_score, max_score), {}))
        return self

    def zcard(self, key: str) -> FakePipeline:
        self.ops.append(("zcard", (key,), {}))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> FakePipeline:
        self.ops.append(("zadd", (key, mapping), {}))
        return self

    def expire(self, key: str, ttl: int) -> FakePipeline:
        self.ops.append(("expire", (key, ttl), {}))
        return self

    async def execute(self) -> list[object]:
        out: list[object] = []
        for op, args, kwargs in self.ops:
            fn = getattr(self.redis, op)
            out.append(await fn(*args, **kwargs))
        self.ops.clear()
        return out


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.expires: dict[str, float] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def _purge(self, key: str) -> None:
        exp = self.expires.get(key)
        if exp is not None and exp <= time.time():
            self.values.pop(key, None)
            self.sorted_sets.pop(key, None)
            self.expires.pop(key, None)

    async def get(self, key: str):
        self._purge(key)
        return self.values.get(key)

    async def set(self, key: str, value: object):
        self.values[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: object):
        self.values[key] = value
        self.expires[key] = time.time() + ttl
        return True

    async def ttl(self, key: str) -> int:
        self._purge(key)
        if key not in self.expires:
            return -1
        remaining = self.expires[key] - time.time()
        if remaining <= 0:
            return 0
        return int(remaining) if remaining.is_integer() else int(remaining) + 1

    async def incr(self, key: str) -> int:
        self._purge(key)
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key: str, ttl: int):
        self.expires[key] = time.time() + ttl
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values or key in self.sorted_sets:
                deleted += 1
            self.values.pop(key, None)
            self.sorted_sets.pop(key, None)
            self.expires.pop(key, None)
        return deleted

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        items = self.sorted_sets.get(key, {})
        remove_keys = [m for m, s in items.items() if min_score <= s <= max_score]
        for member in remove_keys:
            items.pop(member, None)
        self.sorted_sets[key] = items
        return len(remove_keys)

    async def zcard(self, key: str) -> int:
        return len(self.sorted_sets.get(key, {}))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        items = self.sorted_sets.setdefault(key, {})
        for member, score in mapping.items():
            items[member] = score
        return len(mapping)

    async def zrange(self, key: str, start: int, stop: int, withscores: bool = False):
        items = sorted(self.sorted_sets.get(key, {}).items(), key=lambda item: item[1])
        sliced = items[start:] if stop == -1 else items[start : stop + 1]
        if withscores:
            return [(member, score) for member, score in sliced]
        return [member for member, _ in sliced]

    async def scan(self, cursor: int, match: str, count: int = 100):
        keys = [k for k in self.values if fnmatch.fnmatch(k, match)]
        return 0, keys[:count]


class StubPromptRepo:
    def __init__(self, versions: list[PromptVersion]) -> None:
        self.versions = versions

    async def list_active(self) -> list[PromptVersion]:
        return self.versions


@pytest.mark.asyncio
async def test_circuit_breaker_transitions() -> None:
    redis = FakeRedis()
    breaker = CircuitBreaker(redis, CircuitBreakerConfig(max_failures=2, cooldown_seconds=1))

    assert await breaker.can_attempt("anthropic") is True
    await breaker.record_failure("anthropic")
    assert await breaker.state("anthropic") == "closed"

    await breaker.record_failure("anthropic")
    assert await breaker.state("anthropic") == "open"
    assert await breaker.can_attempt("anthropic") is False

    await redis.set("cina:provider:anthropic:cooldown", "1")
    redis.expires["cina:provider:anthropic:cooldown"] = time.time() - 1
    assert await breaker.can_attempt("anthropic") is True
    assert await breaker.state("anthropic") == "half-open"

    await breaker.record_success("anthropic")
    assert await breaker.state("anthropic") == "closed"


@pytest.mark.asyncio
async def test_rate_limiter_burst_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    limiter = RateLimiter(redis, requests_per_minute=2)

    now = 1_700_000_000.0
    monkeypatch.setattr("cina.orchestration.limits.rate_limiter.time.time", lambda: now)

    first = await limiter.check("tenant-a")
    second = await limiter.check("tenant-a")
    third = await limiter.check("tenant-a")

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.retry_after_seconds >= 1


@pytest.mark.asyncio
async def test_lsh_hash_is_deterministic() -> None:
    redis = FakeRedis()
    hasher = LSHHasher(redis, num_hyperplanes=16, dimensions=4, seed=11)

    embedding = [0.1, 0.2, -0.3, 0.4]
    h1 = await hasher.hash_embedding(embedding)
    h2 = await hasher.hash_embedding(embedding)

    assert h1 == h2
    assert len(h1) == 4


@pytest.mark.asyncio
async def test_prompt_router_respects_weights() -> None:
    repo = StubPromptRepo(
        [
            PromptVersion("v1", "prompt-a", None, 0.0, True),
            PromptVersion("v2", "prompt-b", None, 1.0, True),
        ]
    )
    router = PromptRouter(repo, default_version="v1")

    chosen = await router.choose()
    assert chosen.version_id == "v2"
    assert chosen.system_prompt == "prompt-b"
