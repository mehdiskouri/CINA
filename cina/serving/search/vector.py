"""Vector search — pgvector cosine similarity with configurable ef_search."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

from cina.models.search import SearchResult
from cina.observability.logging import get_logger
from cina.observability.metrics import cina_query_latency_seconds

if TYPE_CHECKING:
    import asyncpg

log = get_logger("cina.serving.search.vector")


class VectorSearcher:
    """Async pgvector cosine-similarity search with HNSW ef_search tuning."""

    def __init__(self, pool: asyncpg.Pool, *, ef_search: int = 100) -> None:
        """Initialize vector searcher with pool and HNSW ef_search parameter."""
        self.pool = pool
        self.ef_search = ef_search

    async def search(self, embedding: list[float], top_k: int) -> list[SearchResult]:
        """Run vector similarity search and return top results."""
        start = time.perf_counter()
        vector_str = "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(f"SET LOCAL hnsw.ef_search = {self.ef_search}")
                rows = await conn.fetch(
                    """
                    SELECT id, content, token_count, metadata,
                           1 - (embedding <=> $1::vector) AS score
                    FROM chunks
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> $1::vector
                    LIMIT $2
                    """,
                    vector_str,
                    top_k,
                )
            results = [
                SearchResult(
                    chunk_id=row["id"],
                    content=row["content"],
                    token_count=row["token_count"],
                    metadata=_metadata_to_dict(row["metadata"]),
                    score=float(row["score"]),
                )
                for row in rows
            ]
        finally:
            elapsed = time.perf_counter() - start
            cina_query_latency_seconds.labels(stage="vector_search").observe(elapsed)

        log.debug(
            "vector_search",
            top_k=top_k,
            returned=len(results),
            elapsed_ms=round(elapsed * 1000, 1),
        )
        return results


def _metadata_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
        return {}
    return {}
