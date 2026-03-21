"""Cost event persistence repository."""

from __future__ import annotations

import asyncpg


class CostEventRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def insert(
        self,
        *,
        query_id: str,
        tenant_id: str | None,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
        cache_hit: bool,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cost_events (
                    query_id,
                    tenant_id,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    estimated_cost_usd,
                    cache_hit
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8)
                """,
                query_id,
                tenant_id,
                provider,
                model,
                input_tokens,
                output_tokens,
                estimated_cost_usd,
                cache_hit,
            )
