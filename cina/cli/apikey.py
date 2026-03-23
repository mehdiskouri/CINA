"""CLI commands for API key lifecycle operations."""

from __future__ import annotations

import asyncio
import secrets

import bcrypt
import typer

from cina.db.connection import create_pool
from cina.db.repositories.apikey import APIKeyRepository

app = typer.Typer(help="API key commands")


def _new_key() -> str:
    """Generate a new opaque API key token."""
    return "cina_sk_" + secrets.token_urlsafe(32)


@app.command("create")
def create(
    tenant: str = typer.Option(..., "--tenant", help="Tenant identifier"),
    name: str = typer.Option("default", "--name", help="Human-friendly key name"),
) -> None:
    """Create and print a new API key for a tenant."""

    async def _run() -> None:
        pool = await create_pool()
        try:
            repo = APIKeyRepository(pool)
            plain = _new_key()
            key_hash = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            key_id = await repo.create_key(key_hash=key_hash, tenant_id=tenant, name=name)
            typer.echo(f"id={key_id}")
            typer.echo(f"api_key={plain}")
        finally:
            await pool.close()

    asyncio.run(_run())


@app.command("revoke")
def revoke(key_id: str = typer.Option(..., "--id", help="API key UUID")) -> None:
    """Revoke an API key by id."""

    async def _run() -> None:
        pool = await create_pool()
        try:
            repo = APIKeyRepository(pool)
            ok = await repo.revoke_key(key_id)
            if ok:
                typer.echo("revoked")
            else:
                typer.echo("not_found_or_inactive")
        finally:
            await pool.close()

    asyncio.run(_run())


@app.command("list")
def list_keys(tenant: str | None = typer.Option(None, "--tenant", help="Filter by tenant")) -> None:
    """List API keys, optionally filtered by tenant."""

    async def _run() -> None:
        pool = await create_pool()
        try:
            repo = APIKeyRepository(pool)
            rows = await repo.list_keys(tenant)
            for row in rows:
                line = (
                    f"id={row['id']} tenant={row['tenant_id']} "
                    f"name={row['name']} active={row['active']}"
                )
                typer.echo(
                    line,
                )
        finally:
            await pool.close()

    asyncio.run(_run())
