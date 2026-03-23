"""Cost event persistence repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


@dataclass(slots=True)
class CostEventInsert:
    """Payload for writing a single cost event."""

    query_id: str
    tenant_id: str | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    cache_hit: bool


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


def _as_float(value: object) -> float:
    """Convert loosely typed legacy values to float."""
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return float(str(value))


class CostEventRepository:
    """Data access layer for cost event records."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize repository with a database pool."""
        self.pool = pool

    async def insert(self, event: CostEventInsert | None = None, /, **kwargs: object) -> None:
        """Insert a cost event row."""
        if event is None:
            event = CostEventInsert(
                query_id=str(kwargs["query_id"]),
                tenant_id=(
                    str(kwargs["tenant_id"]) if kwargs.get("tenant_id") is not None else None
                ),
                provider=str(kwargs["provider"]),
                model=str(kwargs["model"]),
                input_tokens=_as_int(kwargs["input_tokens"]),
                output_tokens=_as_int(kwargs["output_tokens"]),
                estimated_cost_usd=_as_float(kwargs["estimated_cost_usd"]),
                cache_hit=bool(kwargs["cache_hit"]),
            )
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
                event.query_id,
                event.tenant_id,
                event.provider,
                event.model,
                event.input_tokens,
                event.output_tokens,
                event.estimated_cost_usd,
                event.cache_hit,
            )
