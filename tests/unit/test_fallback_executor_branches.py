from __future__ import annotations

import asyncio

import pytest

from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.orchestration.providers.protocol import ProviderServerError
from cina.orchestration.routing.fallback import ConcurrentFallbackExecutor


class FakeProvider:
    def __init__(
        self,
        name: str,
        chunks: list[str],
        *,
        delay: float = 0.0,
        exc: Exception | None = None,
    ) -> None:
        self.name = name
        self.chunks = chunks
        self.delay = delay
        self.exc = exc

    async def complete(self, _messages: list[Message], _config: CompletionConfig):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        for chunk in self.chunks:
            yield StreamChunk(text=chunk)


class FakeRouter:
    def __init__(
        self,
        primary_name: str,
        primary_provider,
        fallback_name: str,
        fallback_provider,
    ) -> None:
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider
        self.success: list[str] = []
        self.failure: list[str] = []

    async def select_primary(self):
        return type("Selection", (), {"name": self.primary_name, "provider": self.primary_provider})

    async def select_fallback(self):
        return type(
            "Selection",
            (),
            {"name": self.fallback_name, "provider": self.fallback_provider},
        )

    async def record_success(self, provider_name: str) -> None:
        self.success.append(provider_name)

    async def record_failure(self, provider_name: str) -> None:
        self.failure.append(provider_name)


@pytest.mark.asyncio
async def test_executor_uses_fallback_when_primary_selection_is_already_fallback() -> None:
    fallback = FakeProvider("openai", ["F"])
    router = FakeRouter("anthropic", FakeProvider("anthropic", ["P"]), "openai", fallback)
    router.primary_name = "openai"

    executor = ConcurrentFallbackExecutor(router, ttft_threshold_seconds=0.01)
    result = await executor.complete([Message(role="user", content="q")], CompletionConfig())
    tokens = [c.text async for c in result.stream]

    assert result.provider_name == "openai"
    assert result.fallback_triggered is False
    assert tokens == ["P"]


@pytest.mark.asyncio
async def test_executor_handles_primary_stop_iteration_path() -> None:
    primary = FakeProvider("anthropic", [])
    fallback = FakeProvider("openai", ["F"])
    router = FakeRouter("anthropic", primary, "openai", fallback)

    executor = ConcurrentFallbackExecutor(router, ttft_threshold_seconds=0.05)
    result = await executor.complete([Message(role="user", content="q")], CompletionConfig())

    tokens = [c.text async for c in result.stream]
    assert tokens == []
    assert result.provider_name == "anthropic"


@pytest.mark.asyncio
async def test_executor_primary_timeout_then_fallback_failure_raises_provider_error() -> None:
    primary = FakeProvider("anthropic", ["P"], delay=0.2)
    fallback = FakeProvider("openai", [], exc=ProviderServerError("bad", provider="openai"))
    router = FakeRouter("anthropic", primary, "openai", fallback)

    executor = ConcurrentFallbackExecutor(router, ttft_threshold_seconds=0.01)

    with pytest.raises(ProviderServerError):
        await executor.complete([Message(role="user", content="q")], CompletionConfig())


@pytest.mark.asyncio
async def test_executor_primary_immediate_exception_uses_fallback_stream() -> None:
    primary = FakeProvider("anthropic", [], exc=RuntimeError("boom"))
    fallback = FakeProvider("openai", ["F"])
    router = FakeRouter("anthropic", primary, "openai", fallback)

    executor = ConcurrentFallbackExecutor(router, ttft_threshold_seconds=0.05)
    result = await executor.complete([Message(role="user", content="q")], CompletionConfig())

    tokens = [c.text async for c in result.stream]
    assert result.provider_name == "openai"
    assert result.fallback_triggered is True
    assert tokens == ["F"]
