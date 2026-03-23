"""Repository for API key creation, revocation, and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import bcrypt

if TYPE_CHECKING:
    import asyncpg


@dataclass(slots=True)
class APIKeyRecord:
    """Resolved API key identity returned after token validation."""

    id: UUID
    tenant_id: str
    name: str


class APIKeyRepository:
    """Data access layer for API key records."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize repository with a database pool."""
        self.pool = pool

    async def create_key(self, *, key_hash: str, tenant_id: str, name: str) -> UUID:
        """Create a new active API key row and return its id."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO api_keys (key_hash, tenant_id, name, active)
                VALUES ($1, $2, $3, true)
                RETURNING id
                """,
                key_hash,
                tenant_id,
                name,
            )
        if row is None:
            message = "Failed to create API key"
            raise RuntimeError(message)
        return UUID(str(row["id"]))

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key by id, returning whether a row changed."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE api_keys
                SET active = false, revoked_at = now()
                WHERE id = $1::uuid AND active = true
                """,
                key_id,
            )
        return str(result).endswith("1")

    async def list_keys(self, tenant_id: str | None = None) -> list[dict[str, object]]:
        """List API keys, optionally filtered by tenant id."""
        query = """
            SELECT id, tenant_id, name, active, created_at, revoked_at
            FROM api_keys
        """
        args: tuple[object, ...] = ()
        if tenant_id:
            query += " WHERE tenant_id = $1"
            args = (tenant_id,)
        query += " ORDER BY created_at DESC"

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]

    async def validate_token(self, token: str) -> APIKeyRecord | None:
        """Validate a plaintext token and return matching key identity."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, key_hash, tenant_id, name
                FROM api_keys
                WHERE active = true
                """,
            )
        token_bytes = token.encode("utf-8")
        for row in rows:
            key_hash = row["key_hash"]
            hash_bytes = key_hash.encode("utf-8") if isinstance(key_hash, str) else bytes(key_hash)
            if bcrypt.checkpw(token_bytes, hash_bytes):
                return APIKeyRecord(
                    id=row["id"],
                    tenant_id=str(row["tenant_id"]),
                    name=str(row["name"]),
                )
        return None
