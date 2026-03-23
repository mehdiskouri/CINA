"""SQS implementation of the ingestion queue protocol."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import aioboto3

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class SQSQueue:
    """Queue backend that reads/writes messages through AWS SQS."""

    def __init__(
        self,
        *,
        queue_url_env: str = "SQS_QUEUE_URL",
        dlq_url_env: str = "SQS_DLQ_URL",
        region_env: str = "AWS_REGION",
        endpoint_url_env: str = "AWS_SQS_ENDPOINT_URL",
    ) -> None:
        """Initialize SQS queue backend with environment variable keys."""
        self.queue_url_env = queue_url_env
        self.dlq_url_env = dlq_url_env
        self.region_env = region_env
        self.endpoint_url_env = endpoint_url_env
        self._session = aioboto3.Session()

    def _queue_url(self) -> str:
        """Resolve primary queue URL from environment."""
        queue_url = os.getenv(self.queue_url_env)
        if not queue_url:
            message = f"Missing SQS queue URL env var: {self.queue_url_env}"
            raise RuntimeError(message)
        return queue_url

    def _dlq_url(self) -> str:
        """Resolve dead-letter queue URL or fallback to primary queue."""
        return os.getenv(self.dlq_url_env) or self._queue_url()

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        """Yield an SQS client with optional endpoint/region overrides."""
        kwargs: dict[str, str] = {}
        region = os.getenv(self.region_env)
        endpoint_url = os.getenv(self.endpoint_url_env)
        if region:
            kwargs["region_name"] = region
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url

        async with self._session.client("sqs", **kwargs) as client:
            yield client

    async def enqueue(self, message: dict[str, object], queue_name: str) -> str:
        """Enqueue one message in SQS and return message id."""
        del queue_name
        async with self._client() as client:
            response = await client.send_message(
                QueueUrl=self._queue_url(),
                MessageBody=json.dumps(message, ensure_ascii=True),
            )
        message_id = response.get("MessageId")
        return str(message_id) if message_id is not None else ""

    async def dequeue(
        self,
        queue_name: str,
        wait_timeout_seconds: int,
    ) -> dict[str, object] | None:
        """Dequeue at most one message from SQS."""
        del queue_name
        async with self._client() as client:
            response = await client.receive_message(
                QueueUrl=self._queue_url(),
                MaxNumberOfMessages=1,
                WaitTimeSeconds=max(1, wait_timeout_seconds),
                VisibilityTimeout=300,
                MessageAttributeNames=["All"],
            )

        messages = response.get("Messages", [])
        if not messages:
            return None

        msg = messages[0]
        body = msg.get("Body")
        if not isinstance(body, str):
            return None

        loaded = json.loads(body)
        if not isinstance(loaded, dict):
            return None
        payload: dict[str, object] = {str(key): value for key, value in loaded.items()}

        receipt = msg.get("ReceiptHandle")
        if not isinstance(receipt, str) or not receipt:
            return None
        payload["__receipt"] = receipt
        return payload

    async def acknowledge(self, receipt: str) -> None:
        """Acknowledge a dequeued SQS message."""
        async with self._client() as client:
            await client.delete_message(
                QueueUrl=self._queue_url(),
                ReceiptHandle=receipt,
            )

    async def dead_letter(self, message: dict[str, object], queue_name: str, reason: str) -> None:
        """Send a failed message to DLQ with a reason."""
        del queue_name
        payload = dict(message)
        payload["dead_letter_reason"] = reason
        async with self._client() as client:
            await client.send_message(
                QueueUrl=self._dlq_url(),
                MessageBody=json.dumps(payload, ensure_ascii=True),
            )
