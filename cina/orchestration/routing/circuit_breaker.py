"""Redis-backed circuit breaker for provider routing."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis


@dataclass(slots=True)
class CircuitBreakerConfig:
    max_failures: int
    cooldown_seconds: int


class CircuitBreaker:
    def __init__(self, redis: Redis, config: CircuitBreakerConfig) -> None:
        self.redis = redis
        self.config = config

    @staticmethod
    def _failures_key(provider: str) -> str:
        return f"cina:provider:{provider}:failures"

    @staticmethod
    def _circuit_key(provider: str) -> str:
        return f"cina:provider:{provider}:circuit"

    @staticmethod
    def _cooldown_key(provider: str) -> str:
        return f"cina:provider:{provider}:cooldown"

    async def state(self, provider: str) -> str:
        circuit = await self.redis.get(self._circuit_key(provider))
        if circuit is None:
            return "closed"
        return circuit.decode("utf-8") if isinstance(circuit, bytes) else str(circuit)

    async def can_attempt(self, provider: str) -> bool:
        state = await self.state(provider)
        if state == "closed":
            return True
        if state == "half-open":
            return True
        cooldown_ttl = await self.redis.ttl(self._cooldown_key(provider))
        if cooldown_ttl <= 0:
            await self.redis.set(self._circuit_key(provider), "half-open")
            return True
        return False

    async def record_success(self, provider: str) -> None:
        pipe = self.redis.pipeline()
        pipe.delete(self._failures_key(provider))
        pipe.delete(self._cooldown_key(provider))
        pipe.set(self._circuit_key(provider), "closed")
        await pipe.execute()

    async def record_failure(self, provider: str) -> None:
        key = self._failures_key(provider)
        failures = await self.redis.incr(key)
        await self.redis.expire(key, self.config.cooldown_seconds)

        state = await self.state(provider)
        if state == "half-open" or failures >= self.config.max_failures:
            pipe = self.redis.pipeline()
            pipe.set(self._circuit_key(provider), "open")
            pipe.setex(self._cooldown_key(provider), self.config.cooldown_seconds, "1")
            await pipe.execute()
