"""BM25 search — PostgreSQL full-text search with ts_rank_cd scoring."""

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

log = get_logger("cina.serving.search.bm25")


class BM25Searcher:
    """Async PostgreSQL full-text search using tsvector/tsquery."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize BM25 searcher with database pool."""
        self.pool = pool

    async def search(self, query: str, top_k: int) -> list[SearchResult]:
        """Run full-text search and return top-ranked chunk matches."""
        start = time.perf_counter()
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, content, token_count, metadata,
                           ts_rank_cd(content_tsvector, plainto_tsquery('english', $1)) AS score
                    FROM chunks
                    WHERE content_tsvector @@ plainto_tsquery('english', $1)
                    ORDER BY score DESC
                    LIMIT $2
                    """,
                    query,
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
            cina_query_latency_seconds.labels(stage="bm25_search").observe(elapsed)

        log.debug(
            "bm25_search",
            query=query[:80],
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
