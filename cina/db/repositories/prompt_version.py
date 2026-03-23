"""Prompt version repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


@dataclass(slots=True)
class PromptVersion:
    """Prompt configuration used for request routing."""

    id: str
    system_prompt: str
    description: str | None
    traffic_weight: float
    active: bool


class PromptVersionRepository:
    """Data access layer for prompt version records."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize repository with a database pool."""
        self.pool = pool

    async def list_active(self) -> list[PromptVersion]:
        """Return all active prompt versions ordered by id."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, system_prompt, description, traffic_weight, active
                FROM prompt_versions
                WHERE active = true
                ORDER BY id
                """,
            )
        return [
            PromptVersion(
                id=str(r["id"]),
                system_prompt=str(r["system_prompt"]),
                description=r["description"],
                traffic_weight=float(r["traffic_weight"]),
                active=bool(r["active"]),
            )
            for r in rows
        ]

    async def upsert(
        self,
        *,
        version_id: str,
        system_prompt: str,
        description: str | None,
        traffic_weight: float,
        active: bool,
    ) -> None:
        """Insert or update a prompt version row by id."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO prompt_versions (id, system_prompt, description, traffic_weight, active)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id)
                DO UPDATE SET
                    system_prompt = EXCLUDED.system_prompt,
                    description = EXCLUDED.description,
                    traffic_weight = EXCLUDED.traffic_weight,
                    active = EXCLUDED.active
                """,
                version_id,
                system_prompt,
                description,
                traffic_weight,
                active,
            )
