from __future__ import annotations

from typing import Protocol


class EmbeddingProviderProtocol(Protocol):
    async def embed(self, texts: list[str], model: str, dimensions: int) -> list[list[float]]: ...

    async def health_check(self) -> bool: ...
