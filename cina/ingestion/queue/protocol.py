"""Protocol contract for ingestion queue backends."""

from __future__ import annotations

from typing import Protocol


class QueueProtocol(Protocol):
    """Interface required by queue backends used in ingestion."""

    async def enqueue(self, message: dict[str, object], queue_name: str) -> str:
        """Enqueue a message and return backend-specific message id."""
        ...

    async def dequeue(
        self,
        queue_name: str,
        wait_timeout_seconds: int,
    ) -> dict[str, object] | None:
        """Read one pending message or return `None` when queue is idle."""
        ...

    async def acknowledge(self, receipt: str) -> None:
        """Acknowledge successful processing for a received message."""
        ...

    async def dead_letter(
        self,
        message: dict[str, object],
        queue_name: str,
        reason: str,
    ) -> None:
        """Move a failed message to the dead-letter queue with a reason."""
        ...
