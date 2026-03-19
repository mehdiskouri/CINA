"""SSE event formatting and keepalive utilities."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator


def sse_event(event: str, data: dict[str, object]) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def sse_keepalive() -> str:
    """SSE comment line used as a keepalive to prevent connection drops."""
    return ":keepalive\n\n"


async def merge_with_keepalive(
    stream: AsyncIterator[str],
    interval_seconds: int = 15,
) -> AsyncIterator[str]:
    """Merge an SSE token stream with periodic keepalive comments.

    Yields events from ``stream`` as they arrive, interspersing keepalive
    comments whenever ``interval_seconds`` elapses without a new event.
    """
    # We drive the stream via an async queue so we can race against a timer
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _reader() -> None:
        try:
            async for item in stream:
                await queue.put(item)
        finally:
            await queue.put(None)  # sentinel

    reader_task = asyncio.create_task(_reader())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval_seconds)
            except TimeoutError:
                yield sse_keepalive()
                continue
            if item is None:
                break
            yield item
    finally:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task
