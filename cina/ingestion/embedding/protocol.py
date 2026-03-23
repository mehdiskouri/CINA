"""Protocol contract for pluggable embedding providers."""

from __future__ import annotations

from typing import Protocol


class EmbeddingProviderProtocol(Protocol):
    """Interface implemented by embedding backends."""

    async def embed(self, texts: list[str], model: str, dimensions: int) -> list[list[float]]:
        """Embed a batch of texts into fixed-dimension vectors."""
        ...

    async def health_check(self) -> bool:
        """Return whether provider health checks succeed."""
        ...
