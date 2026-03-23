"""CLI commands for dead-letter queue inspection and recovery."""

import asyncio
import json
import os
from typing import Any

import typer
from redis.asyncio import Redis

from cina.config import load_config

app = typer.Typer(help="DLQ commands")


def _redis_url() -> str:
    """Resolve the Redis URL for queue operations."""
    cfg = load_config()
    return os.getenv(cfg.database.redis.url_env, "redis://localhost:6379/0")


def _decode_message_id(message_id: bytes | str) -> str:
    """Normalize Redis stream message id to text."""
    return message_id.decode() if isinstance(message_id, bytes) else message_id


@app.command("list")
def list_dlq(
    queue: str = typer.Option("ingestion", "--queue", help="Queue name prefix"),
    limit: int = typer.Option(50, "--limit", help="Max entries"),
) -> None:
    """List the newest dead-letter queue entries."""

    async def _run() -> None:
        redis = Redis.from_url(_redis_url())
        try:
            rows = await redis.xrevrange(f"{queue}:dlq", count=limit)
            for msg_id, fields in rows:
                payload_raw = fields.get(b"payload") or fields.get("payload")
                if isinstance(payload_raw, bytes):
                    payload_raw = payload_raw.decode("utf-8")
                payload: dict[str, Any] = json.loads(str(payload_raw)) if payload_raw else {}
                line = f"id={_decode_message_id(msg_id)} payload={payload}"
                typer.echo(
                    line,
                )
        finally:
            await redis.aclose()

    asyncio.run(_run())


@app.command("retry")
def retry_dlq(
    message_id: str = typer.Option(..., "--id", help="DLQ stream message id"),
    queue: str = typer.Option("ingestion", "--queue", help="Queue name prefix"),
) -> None:
    """Retry a DLQ entry by moving it back to the primary queue."""

    async def _run() -> None:
        redis = Redis.from_url(_redis_url())
        dlq_name = f"{queue}:dlq"
        try:
            rows = await redis.xrange(dlq_name, min=message_id, max=message_id, count=1)
            if not rows:
                typer.echo("not_found")
                return
            _, fields = rows[0]
            payload_raw = fields.get(b"payload") or fields.get("payload")
            if isinstance(payload_raw, bytes):
                payload_raw = payload_raw.decode("utf-8")
            payload = json.loads(str(payload_raw)) if payload_raw else {}
            await redis.xadd(queue, {"payload": json.dumps(payload, ensure_ascii=True)})
            await redis.xdel(dlq_name, message_id)
            typer.echo("retried")
        finally:
            await redis.aclose()

    asyncio.run(_run())


@app.command("purge")
def purge_dlq(queue: str = typer.Option("ingestion", "--queue", help="Queue name prefix")) -> None:
    """Purge all entries from the DLQ stream."""

    async def _run() -> None:
        redis = Redis.from_url(_redis_url())
        try:
            deleted = await redis.delete(f"{queue}:dlq")
            typer.echo(f"deleted={deleted}")
        finally:
            await redis.aclose()

    asyncio.run(_run())
