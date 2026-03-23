from __future__ import annotations

import pytest

from cina.orchestration.routing.provider_router import ProviderRouter


class FakeBreaker:
    def __init__(self, attempts: dict[str, bool]) -> None:
        self.attempts = attempts
        self.success_calls: list[str] = []
        self.failure_calls: list[str] = []

    async def can_attempt(self, provider_name: str) -> bool:
        return self.attempts[provider_name]

    async def record_success(self, provider_name: str) -> None:
        self.success_calls.append(provider_name)

    async def record_failure(self, provider_name: str) -> None:
        self.failure_calls.append(provider_name)


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.mark.asyncio
async def test_select_primary_uses_primary_when_breaker_allows() -> None:
    router = ProviderRouter(
        primary_name="anthropic",
        primary=FakeProvider("anthropic"),
        fallback_name="openai",
        fallback=FakeProvider("openai"),
        breaker=FakeBreaker({"anthropic": True, "openai": True}),
    )

    selection = await router.select_primary()

    assert selection.name == "anthropic"
    assert selection.fallback_triggered is False


@pytest.mark.asyncio
async def test_select_primary_uses_fallback_when_primary_is_open() -> None:
    router = ProviderRouter(
        primary_name="anthropic",
        primary=FakeProvider("anthropic"),
        fallback_name="openai",
        fallback=FakeProvider("openai"),
        breaker=FakeBreaker({"anthropic": False, "openai": True}),
    )

    selection = await router.select_primary()

    assert selection.name == "openai"
    assert selection.fallback_triggered is True


@pytest.mark.asyncio
async def test_select_fallback_prefers_fallback_then_primary() -> None:
    fallback_allowed = ProviderRouter(
        primary_name="anthropic",
        primary=FakeProvider("anthropic"),
        fallback_name="openai",
        fallback=FakeProvider("openai"),
        breaker=FakeBreaker({"anthropic": True, "openai": True}),
    )
    fallback_blocked = ProviderRouter(
        primary_name="anthropic",
        primary=FakeProvider("anthropic"),
        fallback_name="openai",
        fallback=FakeProvider("openai"),
        breaker=FakeBreaker({"anthropic": True, "openai": False}),
    )

    first = await fallback_allowed.select_fallback()
    second = await fallback_blocked.select_fallback()

    assert first.name == "openai"
    assert first.fallback_triggered is True
    assert second.name == "anthropic"
    assert second.fallback_triggered is False


@pytest.mark.asyncio
async def test_record_success_and_failure_delegate_to_breaker() -> None:
    breaker = FakeBreaker({"anthropic": True, "openai": True})
    router = ProviderRouter(
        primary_name="anthropic",
        primary=FakeProvider("anthropic"),
        fallback_name="openai",
        fallback=FakeProvider("openai"),
        breaker=breaker,
    )

    await router.record_success("anthropic")
    await router.record_failure("openai")

    assert breaker.success_calls == ["anthropic"]
    assert breaker.failure_calls == ["openai"]
