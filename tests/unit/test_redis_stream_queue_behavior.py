from __future__ import annotations

import json

import pytest
from redis.exceptions import ResponseError

from cina.ingestion.queue.redis_stream import RedisStreamQueue


class FakeRedis:
    def __init__(self) -> None:
        self.group_create_calls: list[tuple[str, str, str, bool]] = []
        self.xadd_calls: list[tuple[str, dict[str, str]]] = []
        self.xreadgroup_rows = []
        self.xack_calls: list[tuple[str, str, str]] = []

    async def xgroup_create(self, stream: str, group: str, id: str, mkstream: bool) -> None:
        self.group_create_calls.append((stream, group, id, mkstream))

    async def xadd(self, stream: str, fields: dict[str, str]):
        self.xadd_calls.append((stream, fields))
        return b"1-0"

    async def xreadgroup(
        self, group: str, consumer: str, streams: dict[str, str], count: int, block: int
    ):
        _ = (group, consumer, streams, count, block)
        return self.xreadgroup_rows

    async def xack(self, stream: str, group: str, msg_id: str) -> None:
        self.xack_calls.append((stream, group, msg_id))


@pytest.mark.asyncio
async def test_ensure_group_ignores_busygroup() -> None:
    queue = RedisStreamQueue.__new__(RedisStreamQueue)
    queue.group = "g"
    queue.consumer = "c"

    class BusyRedis(FakeRedis):
        async def xgroup_create(self, stream: str, group: str, id: str, mkstream: bool) -> None:
            _ = (stream, group, id, mkstream)
            raise ResponseError("BUSYGROUP Consumer Group name already exists")

    queue.redis = BusyRedis()

    await queue._ensure_group("q")


@pytest.mark.asyncio
async def test_enqueue_dequeue_ack_and_dead_letter() -> None:
    queue = RedisStreamQueue.__new__(RedisStreamQueue)
    queue.group = "g"
    queue.consumer = "consumer-1"
    redis = FakeRedis()
    redis.xreadgroup_rows = [
        (
            b"main-q",
            [
                (
                    b"9-1",
                    {
                        b"payload": b'{"chunk_id":"c1","content":"x","content_hash":"h","embedding_model":"m","embedding_dim":5}'
                    },
                )
            ],
        )
    ]
    queue.redis = redis

    message_id = await queue.enqueue({"hello": "world"}, "main-q")
    received = await queue.dequeue("main-q", wait_timeout_seconds=2)
    await queue.acknowledge(str(received["__receipt"]))
    await queue.dead_letter({"chunk_id": "c1"}, "main-q", reason="boom")

    assert message_id == "1-0"
    assert received is not None
    assert received["chunk_id"] == "c1"
    assert received["__receipt"] == "main-q|9-1"
    assert redis.xack_calls == [("main-q", "g", "9-1")]
    dlq_stream, dlq_fields = redis.xadd_calls[-1]
    assert dlq_stream == "main-q:dlq"
    dlq_payload = json.loads(dlq_fields["payload"])
    assert dlq_payload["dead_letter_reason"] == "boom"


@pytest.mark.asyncio
async def test_dequeue_returns_none_for_empty_rows_or_non_dict_payload() -> None:
    queue = RedisStreamQueue.__new__(RedisStreamQueue)
    queue.group = "g"
    queue.consumer = "c"
    redis = FakeRedis()
    queue.redis = redis

    redis.xreadgroup_rows = []
    assert await queue.dequeue("q", wait_timeout_seconds=1) is None

    redis.xreadgroup_rows = [("q", [("1-0", {"payload": "[1,2,3]"})])]
    assert await queue.dequeue("q", wait_timeout_seconds=1) is None
