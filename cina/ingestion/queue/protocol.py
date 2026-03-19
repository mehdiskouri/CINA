from __future__ import annotations

from typing import Protocol


class QueueProtocol(Protocol):
    async def enqueue(self, message: dict[str, object], queue_name: str) -> str: ...

    async def dequeue(
        self,
        queue_name: str,
        wait_timeout_seconds: int,
    ) -> dict[str, object] | None: ...

    async def acknowledge(self, receipt: str) -> None: ...

    async def dead_letter(
        self, message: dict[str, object], queue_name: str, reason: str
    ) -> None: ...
