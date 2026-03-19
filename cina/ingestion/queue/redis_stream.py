from __future__ import annotations

import json
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from cina.config import load_config


class RedisStreamQueue:
    def __init__(
        self,
        redis_url: str | None = None,
        group: str = "cina-workers",
        consumer: str | None = None,
    ) -> None:
        cfg = load_config()
        self.redis = Redis.from_url(
            redis_url or __import__("os").getenv(cfg.database.redis.url_env, "")
        )
        self.group = group
        self.consumer = consumer or f"consumer-{uuid4()}"

    async def _ensure_group(self, stream_name: str) -> None:
        try:
            await self.redis.xgroup_create(stream_name, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, message: dict[str, object], queue_name: str) -> str:
        payload = json.dumps(message, ensure_ascii=True)
        message_id = await self.redis.xadd(queue_name, {"payload": payload})
        return message_id.decode("utf-8") if isinstance(message_id, bytes) else str(message_id)

    async def dequeue(
        self,
        queue_name: str,
        wait_timeout_seconds: int,
    ) -> dict[str, object] | None:
        await self._ensure_group(queue_name)
        block_ms = max(1, wait_timeout_seconds * 1000)
        rows = await self.redis.xreadgroup(
            self.group,
            self.consumer,
            {queue_name: ">"},
            count=1,
            block=block_ms,
        )
        if not rows:
            return None

        stream_name, entries = rows[0]
        entry_id, fields = entries[0]
        payload_raw = fields.get(b"payload") or fields.get("payload")
        if isinstance(payload_raw, bytes):
            payload_text = payload_raw.decode("utf-8")
        else:
            payload_text = str(payload_raw)

        loaded = json.loads(payload_text)
        if not isinstance(loaded, dict):
            return None
        payload: dict[str, object] = {str(key): value for key, value in loaded.items()}
        payload["__receipt"] = (
            f"{stream_name.decode() if isinstance(stream_name, bytes) else stream_name}|"
            f"{entry_id.decode() if isinstance(entry_id, bytes) else entry_id}"
        )
        return payload

    async def acknowledge(self, receipt: str) -> None:
        stream, message_id = receipt.split("|", maxsplit=1)
        await self.redis.xack(stream, self.group, message_id)

    async def dead_letter(self, message: dict[str, object], queue_name: str, reason: str) -> None:
        dlq_name = f"{queue_name}:dlq"
        payload = dict(message)
        payload["dead_letter_reason"] = reason
        await self.redis.xadd(dlq_name, {"payload": json.dumps(payload, ensure_ascii=True)})
