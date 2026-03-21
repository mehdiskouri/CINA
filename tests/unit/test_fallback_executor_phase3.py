from __future__ import annotations

import asyncio

import pytest

from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.orchestration.routing.fallback import ConcurrentFallbackExecutor


class FakeProvider:
    def __init__(self, name: str, *, delay: float, token: str) -> None:
        self.name = name
        self.delay = delay
        self.token = token

    async def complete(self, messages: list[Message], config: CompletionConfig):
        _ = messages
        _ = config
        await asyncio.sleep(self.delay)
        yield StreamChunk(text=self.token)


class FakeRouter:
    def __init__(self, primary, fallback) -> None:
        self.primary_name = "primary"
        self.fallback_name = "fallback"
        self.primary = primary
        self.fallback = fallback
        self.success: list[str] = []
        self.failure: list[str] = []

    async def select_primary(self):
        return type("Selection", (), {"name": "primary", "provider": self.primary})

    async def select_fallback(self):
        return type(
            "Selection",
            (),
            {"name": "fallback", "provider": self.fallback, "fallback_triggered": True},
        )

    async def record_success(self, provider_name: str) -> None:
        self.success.append(provider_name)

    async def record_failure(self, provider_name: str) -> None:
        self.failure.append(provider_name)


@pytest.mark.asyncio
async def test_fallback_executor_primary_wins() -> None:
    primary = FakeProvider("primary", delay=0.01, token="P")
    fallback = FakeProvider("fallback", delay=0.05, token="F")
    router = FakeRouter(primary, fallback)
    executor = ConcurrentFallbackExecutor(router, ttft_threshold_seconds=0.02)

    result = await executor.complete([Message(role="user", content="hi")], CompletionConfig())
    chunks = [c.text async for c in result.stream]

    assert result.provider_name == "primary"
    assert result.fallback_triggered is False
    assert chunks == ["P"]


@pytest.mark.asyncio
async def test_fallback_executor_fallback_wins_on_timeout() -> None:
    primary = FakeProvider("primary", delay=0.08, token="P")
    fallback = FakeProvider("fallback", delay=0.01, token="F")
    router = FakeRouter(primary, fallback)
    executor = ConcurrentFallbackExecutor(router, ttft_threshold_seconds=0.02)

    result = await executor.complete([Message(role="user", content="hi")], CompletionConfig())
    chunks = [c.text async for c in result.stream]

    assert result.provider_name == "fallback"
    assert result.fallback_triggered is True
    assert chunks == ["F"]
