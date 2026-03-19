"""Query embedding — reuses the ingestion embedding provider for query-time encoding."""

from __future__ import annotations

import time

from cina.config import load_config
from cina.ingestion.embedding.openai import OpenAIEmbeddingProvider
from cina.observability.logging import get_logger
from cina.observability.metrics import cina_query_latency_seconds

log = get_logger("cina.serving.search.embed")


class QueryEmbedder:
    """Embeds query text using the same model/dimensions as the ingestion index."""

    def __init__(self, provider: OpenAIEmbeddingProvider | None = None) -> None:
        cfg = load_config().ingestion.embedding
        self.model = cfg.model
        self.dimensions = cfg.dimensions
        self.provider = provider or OpenAIEmbeddingProvider()

    async def embed(self, text: str) -> list[float]:
        start = time.perf_counter()
        try:
            embeddings = await self.provider.embed(
                [text], model=self.model, dimensions=self.dimensions
            )
            return embeddings[0]
        finally:
            elapsed = time.perf_counter() - start
            cina_query_latency_seconds.labels(stage="embed_query").observe(elapsed)
            log.debug("embed_query", elapsed_ms=round(elapsed * 1000, 1))
