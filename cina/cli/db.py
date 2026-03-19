from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import typer

from cina.config import load_config

app = typer.Typer(help="Database commands")


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "db" / "migrations"


async def _ensure_migration_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


async def _applied_versions(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {str(row["version"]) for row in rows}


async def run_migrations() -> int:
    cfg = load_config()
    dsn = os.getenv(cfg.database.postgres.dsn_env)
    if not dsn:
        raise RuntimeError(f"Missing database DSN env var: {cfg.database.postgres.dsn_env}")

    files = sorted(_migrations_dir().glob("*.sql"))
    if not files:
        typer.echo("No migrations found")
        return 0

    applied_count = 0
    conn = await asyncpg.connect(dsn)
    try:
        await _ensure_migration_table(conn)
        applied = await _applied_versions(conn)
        for file in files:
            version = file.name
            if version in applied:
                continue
            sql = file.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations(version) VALUES($1)", version)
            applied_count += 1
            typer.echo(f"Applied migration: {version}")
    finally:
        await conn.close()

    if applied_count == 0:
        typer.echo("No new migrations")
    return applied_count


@app.command("migrate")
def migrate() -> None:
    """Apply pending SQL migrations."""

    import asyncio

    asyncio.run(run_migrations())
