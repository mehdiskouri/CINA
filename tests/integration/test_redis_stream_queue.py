from __future__ import annotations

import os

import pytest
from redis.asyncio import Redis

from cina.ingestion.queue.redis_stream import RedisStreamQueue

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


async def _redis_available(url: str) -> bool:
    try:
        client = Redis.from_url(url)
        await client.ping()
    except Exception:
        return False
    await client.aclose()
    return True


@pytest.mark.asyncio
async def test_redis_stream_queue_roundtrip() -> None:
    redis_url = os.getenv("REDIS_URL", DEFAULT_REDIS_URL)
    if not await _redis_available(redis_url):
        pytest.skip("Redis is not reachable for integration test")

    queue = RedisStreamQueue(redis_url=redis_url, group="test-group", consumer="test-consumer")
    queue_name = "cina:test:queue"

    await queue._ensure_group(queue_name)
    await queue.enqueue({"chunk_id": "abc", "content": "text", "embedding_dim": 512}, queue_name)
    message = await queue.dequeue(queue_name, wait_timeout_seconds=1)

    assert message is not None
    assert message["chunk_id"] == "abc"
    assert "__receipt" in message

    await queue.acknowledge(str(message["__receipt"]))
