import os

import typer

from cina.config import load_config

app = typer.Typer(help="DLQ commands")


def _redis_url() -> str:
    cfg = load_config()
    return os.getenv(cfg.database.redis.url_env, "redis://localhost:6379/0")


@app.command("list")
def list_dlq(
    queue: str = typer.Option("ingestion", "--queue", help="Queue name prefix"),
    limit: int = typer.Option(50, "--limit", help="Max entries"),
) -> None:
    import asyncio
    import json

    from redis.asyncio import Redis

    async def _run() -> None:
        redis = Redis.from_url(_redis_url())
        try:
            rows = await redis.xrevrange(f"{queue}:dlq", count=limit)
            for msg_id, fields in rows:
                payload_raw = fields.get(b"payload") or fields.get("payload")
                if isinstance(payload_raw, bytes):
                    payload_raw = payload_raw.decode("utf-8")
                payload = json.loads(str(payload_raw)) if payload_raw else {}
                typer.echo(
                    f"id={msg_id.decode() if isinstance(msg_id, bytes) else msg_id} payload={payload}"
                )
        finally:
            await redis.aclose()

    asyncio.run(_run())


@app.command("retry")
def retry_dlq(
    id: str = typer.Option(..., "--id", help="DLQ stream message id"),
    queue: str = typer.Option("ingestion", "--queue", help="Queue name prefix"),
) -> None:
    import asyncio
    import json

    from redis.asyncio import Redis

    async def _run() -> None:
        redis = Redis.from_url(_redis_url())
        dlq_name = f"{queue}:dlq"
        try:
            rows = await redis.xrange(dlq_name, min=id, max=id, count=1)
            if not rows:
                typer.echo("not_found")
                return
            _, fields = rows[0]
            payload_raw = fields.get(b"payload") or fields.get("payload")
            if isinstance(payload_raw, bytes):
                payload_raw = payload_raw.decode("utf-8")
            payload = json.loads(str(payload_raw)) if payload_raw else {}
            await redis.xadd(queue, {"payload": json.dumps(payload, ensure_ascii=True)})
            await redis.xdel(dlq_name, id)
            typer.echo("retried")
        finally:
            await redis.aclose()

    asyncio.run(_run())


@app.command("purge")
def purge_dlq(queue: str = typer.Option("ingestion", "--queue", help="Queue name prefix")) -> None:
    import asyncio

    from redis.asyncio import Redis

    async def _run() -> None:
        redis = Redis.from_url(_redis_url())
        try:
            deleted = await redis.delete(f"{queue}:dlq")
            typer.echo(f"deleted={deleted}")
        finally:
            await redis.aclose()

    asyncio.run(_run())
