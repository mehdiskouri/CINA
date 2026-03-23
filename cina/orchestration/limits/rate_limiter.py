"""Redis sorted-set sliding window rate limiter."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from redis.asyncio import Redis


@dataclass(slots=True)
class RateLimitResult:
    """Result payload returned from a tenant rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RateLimiter:
    """Redis-backed sliding window limiter keyed by tenant."""

    def __init__(self, redis: Redis, *, requests_per_minute: int) -> None:
        """Initialize limiter with Redis and per-minute request cap."""
        self.redis = redis
        self.requests_per_minute = requests_per_minute

    @staticmethod
    def _key(tenant_id: str) -> str:
        return f"cina:ratelimit:{tenant_id}:rpm"

    async def check(self, tenant_id: str) -> RateLimitResult:
        """Check and update tenant request allowance for the current window."""
        key = self._key(tenant_id)
        now = time.time()
        window_start = now - 60.0

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        _, current = await pipe.execute()
        count = int(current)

        if count >= self.requests_per_minute:
            oldest = await self.redis.zrange(key, 0, 0, withscores=True)
            retry_after = 1
            if oldest:
                retry_after = max(1, int(oldest[0][1] + 60 - now))
            return RateLimitResult(
                allowed=False,
                limit=self.requests_per_minute,
                remaining=0,
                retry_after_seconds=retry_after,
            )

        member = f"{now:.6f}:{uuid4()}"
        pipe = self.redis.pipeline()
        pipe.zadd(key, {member: now})
        pipe.expire(key, 120)
        await pipe.execute()

        return RateLimitResult(
            allowed=True,
            limit=self.requests_per_minute,
            remaining=max(0, self.requests_per_minute - (count + 1)),
            retry_after_seconds=0,
        )
