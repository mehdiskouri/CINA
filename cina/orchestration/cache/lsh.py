"""Locality-sensitive hashing for semantic cache keys."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from redis.asyncio import Redis


class LSHHasher:
    """Generates and persists random hyperplanes for embedding LSH."""

    def __init__(
        self,
        redis: Redis,
        *,
        num_hyperplanes: int,
        dimensions: int = 512,
        seed: int = 42,
        redis_key: str = "cina:cache:lsh:hyperplanes",
    ) -> None:
        """Create an LSH hasher backed by Redis-stored hyperplanes."""
        self.redis = redis
        self.num_hyperplanes = num_hyperplanes
        self.dimensions = dimensions
        self.seed = seed
        self.redis_key = redis_key
        self._hyperplanes: np.ndarray | None = None

    async def ensure_hyperplanes(self) -> np.ndarray:
        """Load existing hyperplanes from Redis or create and store them."""
        if self._hyperplanes is not None:
            return self._hyperplanes

        cached = await self.redis.get(self.redis_key)
        if cached:
            cached_text = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
            planes = np.array(json.loads(cached_text), dtype=np.float32)
            self._hyperplanes = planes
            return planes

        rng = np.random.default_rng(self.seed)
        planes = rng.normal(size=(self.num_hyperplanes, self.dimensions)).astype(np.float32)
        await self.redis.set(self.redis_key, json.dumps(planes.tolist(), ensure_ascii=True))
        self._hyperplanes = planes
        return planes

    async def hash_embedding(self, embedding: list[float]) -> str:
        """Project an embedding and return its binary signature as hex."""
        planes = await self.ensure_hyperplanes()
        vector = np.array(embedding, dtype=np.float32)
        projections = np.dot(planes, vector)
        bits = (projections >= 0).astype(np.uint8)

        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)

        width = max(1, self.num_hyperplanes // 4)
        return f"{value:0{width}x}"
