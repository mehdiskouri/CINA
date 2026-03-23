"""Query logging repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


@dataclass(slots=True)
class QueryLogInsert:
    """Payload for writing a query log event."""

    query_id: str
    query_text: str
    prompt_version_id: str
    provider_used: str
    fallback_triggered: bool
    cache_hit: bool
    total_latency_ms: int
    search_latency_ms: int
    rerank_latency_ms: int
    llm_latency_ms: int
    chunks_retrieved: int
    chunks_used: int
    tenant_id: str | None


def _as_int(value: object) -> int:
    """Convert loosely typed legacy values to int."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    return int(str(value))


class QueryLogRepository:
    """Data access layer for query lifecycle records."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize repository with a database pool."""
        self.pool = pool

    async def insert(self, entry: QueryLogInsert | None = None, /, **kwargs: object) -> None:
        """Insert a query log row."""
        if entry is None:
            entry = QueryLogInsert(
                query_id=str(kwargs["query_id"]),
                query_text=str(kwargs["query_text"]),
                prompt_version_id=str(kwargs["prompt_version_id"]),
                provider_used=str(kwargs["provider_used"]),
                fallback_triggered=bool(kwargs["fallback_triggered"]),
                cache_hit=bool(kwargs["cache_hit"]),
                total_latency_ms=_as_int(kwargs["total_latency_ms"]),
                search_latency_ms=_as_int(kwargs["search_latency_ms"]),
                rerank_latency_ms=_as_int(kwargs["rerank_latency_ms"]),
                llm_latency_ms=_as_int(kwargs["llm_latency_ms"]),
                chunks_retrieved=_as_int(kwargs["chunks_retrieved"]),
                chunks_used=_as_int(kwargs["chunks_used"]),
                tenant_id=(
                    str(kwargs["tenant_id"]) if kwargs.get("tenant_id") is not None else None
                ),
            )
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO query_logs (
                    id,
                    query_text,
                    prompt_version_id,
                    provider_used,
                    fallback_triggered,
                    cache_hit,
                    total_latency_ms,
                    search_latency_ms,
                    rerank_latency_ms,
                    llm_latency_ms,
                    chunks_retrieved,
                    chunks_used,
                    tenant_id
                ) VALUES (
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
                )
                """,
                entry.query_id,
                entry.query_text,
                entry.prompt_version_id,
                entry.provider_used,
                entry.fallback_triggered,
                entry.cache_hit,
                entry.total_latency_ms,
                entry.search_latency_ms,
                entry.rerank_latency_ms,
                entry.llm_latency_ms,
                entry.chunks_retrieved,
                entry.chunks_used,
                entry.tenant_id,
            )
