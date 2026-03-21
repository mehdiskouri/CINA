"""Prompt version repository."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(slots=True)
class PromptVersion:
    id: str
    system_prompt: str
    description: str | None
    traffic_weight: float
    active: bool


class PromptVersionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def list_active(self) -> list[PromptVersion]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, system_prompt, description, traffic_weight, active
                FROM prompt_versions
                WHERE active = true
                ORDER BY id
                """
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
