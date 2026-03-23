from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.orchestration.middleware import compose


async def _final_handler(
    messages: list[Message],
    config: CompletionConfig,
) -> AsyncIterator[StreamChunk]:
    _ = (messages, config)
    yield StreamChunk(text="final")


def _mw(prefix: str, calls: list[str]):
    async def _inner(messages: list[Message], config: CompletionConfig, nxt):
        calls.append(f"{prefix}-before")
        async for chunk in nxt(messages, config):
            yield StreamChunk(text=f"{prefix}:{chunk.text}")
        calls.append(f"{prefix}-after")

    return _inner


@pytest.mark.asyncio
async def test_compose_wraps_middlewares_in_declaration_order() -> None:
    calls: list[str] = []
    handler = compose(_mw("one", calls), _mw("two", calls))(_final_handler)

    out = [
        chunk.text
        async for chunk in handler([Message(role="user", content="q")], CompletionConfig())
    ]

    assert out == ["one:two:final"]
    assert calls == ["one-before", "two-before", "two-after", "one-after"]
