"""Provider selection and routing with circuit-breaker awareness."""

from __future__ import annotations

from dataclasses import dataclass

from cina.observability.metrics import cina_provider_fallback_total
from cina.orchestration.providers.protocol import LLMProviderProtocol
from cina.orchestration.routing.circuit_breaker import CircuitBreaker


@dataclass(slots=True)
class ProviderSelection:
    name: str
    provider: LLMProviderProtocol
    fallback_triggered: bool = False


class ProviderRouter:
    def __init__(
        self,
        *,
        primary_name: str,
        primary: LLMProviderProtocol,
        fallback_name: str,
        fallback: LLMProviderProtocol,
        breaker: CircuitBreaker,
    ) -> None:
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.primary = primary
        self.fallback = fallback
        self.breaker = breaker

    async def select_primary(self) -> ProviderSelection:
        if await self.breaker.can_attempt(self.primary_name):
            return ProviderSelection(name=self.primary_name, provider=self.primary)

        cina_provider_fallback_total.inc()
        return ProviderSelection(
            name=self.fallback_name,
            provider=self.fallback,
            fallback_triggered=True,
        )

    async def select_fallback(self) -> ProviderSelection:
        cina_provider_fallback_total.inc()
        if await self.breaker.can_attempt(self.fallback_name):
            return ProviderSelection(
                name=self.fallback_name,
                provider=self.fallback,
                fallback_triggered=True,
            )
        return ProviderSelection(
            name=self.primary_name,
            provider=self.primary,
            fallback_triggered=False,
        )

    async def record_success(self, provider_name: str) -> None:
        await self.breaker.record_success(provider_name)

    async def record_failure(self, provider_name: str) -> None:
        await self.breaker.record_failure(provider_name)
