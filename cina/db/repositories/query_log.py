"""Query logging repository."""

from __future__ import annotations

import asyncpg


class QueryLogRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def insert(
        self,
        *,
        query_id: str,
        query_text: str,
        prompt_version_id: str,
        provider_used: str,
        fallback_triggered: bool,
        cache_hit: bool,
        total_latency_ms: int,
        search_latency_ms: int,
        rerank_latency_ms: int,
        llm_latency_ms: int,
        chunks_retrieved: int,
        chunks_used: int,
        tenant_id: str | None,
    ) -> None:
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
                query_id,
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
                tenant_id,
            )
