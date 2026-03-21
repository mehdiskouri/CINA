from __future__ import annotations

from typing import Any

import pytest

from cina.ingestion.queue.sqs import SQSQueue


class FakeSQSClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.deleted_messages: list[dict[str, Any]] = []
        self.received_payload: dict[str, Any] = {
            "Messages": [
                {
                    "Body": '{"chunk_id":"abc","content":"txt","content_hash":"h1","embedding_model":"m1","embedding_dim":512}',
                    "ReceiptHandle": "rh-1",
                }
            ]
        }

    async def send_message(self, **kwargs: Any) -> dict[str, Any]:
        self.sent_messages.append(kwargs)
        return {"MessageId": "msg-1"}

    async def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        return self.received_payload

    async def delete_message(self, **kwargs: Any) -> dict[str, Any]:
        self.deleted_messages.append(kwargs)
        return {}


class FakeClientContext:
    def __init__(self, client: FakeSQSClient) -> None:
        self._client = client

    async def __aenter__(self) -> FakeSQSClient:
        return self._client

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        _ = (exc_type, exc, tb)


class FakeSession:
    def __init__(self, client: FakeSQSClient) -> None:
        self._client = client

    def client(self, *_args: Any, **_kwargs: Any) -> FakeClientContext:
        return FakeClientContext(self._client)


@pytest.mark.asyncio
async def test_sqs_enqueue_dequeue_ack_dead_letter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/main")
    monkeypatch.setenv("SQS_DLQ_URL", "https://sqs.us-east-1.amazonaws.com/123/dlq")

    client = FakeSQSClient()
    queue = SQSQueue()
    queue._session = FakeSession(client)  # type: ignore[assignment]

    message_id = await queue.enqueue({"hello": "world"}, "ignored")
    assert message_id == "msg-1"

    item = await queue.dequeue("ignored", wait_timeout_seconds=2)
    assert item is not None
    assert item["chunk_id"] == "abc"
    assert item["__receipt"] == "rh-1"

    await queue.acknowledge("rh-1")
    await queue.dead_letter({"chunk_id": "abc"}, "ignored", reason="failure")

    assert client.deleted_messages[0]["ReceiptHandle"] == "rh-1"
    assert client.sent_messages[-1]["QueueUrl"] == "https://sqs.us-east-1.amazonaws.com/123/dlq"


@pytest.mark.asyncio
async def test_sqs_dequeue_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/main")

    client = FakeSQSClient()
    client.received_payload = {}
    queue = SQSQueue()
    queue._session = FakeSession(client)  # type: ignore[assignment]

    item = await queue.dequeue("ignored", wait_timeout_seconds=1)
    assert item is None
