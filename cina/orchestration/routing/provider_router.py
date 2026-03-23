"""Provider selection and routing with circuit-breaker awareness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cina.observability.metrics import cina_provider_fallback_total

if TYPE_CHECKING:
    from cina.orchestration.providers.protocol import LLMProviderProtocol
    from cina.orchestration.routing.circuit_breaker import CircuitBreaker


@dataclass(slots=True)
class ProviderSelection:
    """Chosen provider details for a single routed request."""

    name: str
    provider: LLMProviderProtocol
    fallback_triggered: bool = False


class ProviderRouter:
    """Selects providers using circuit-breaker state and fallback policy."""

    def __init__(
        self,
        *,
        primary_name: str,
        primary: LLMProviderProtocol,
        fallback_name: str,
        fallback: LLMProviderProtocol,
        breaker: CircuitBreaker,
    ) -> None:
        """Initialize router with primary/fallback providers and breaker."""
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.primary = primary
        self.fallback = fallback
        self.breaker = breaker

    async def select_primary(self) -> ProviderSelection:
        """Select primary unless breaker disallows attempts."""
        if await self.breaker.can_attempt(self.primary_name):
            return ProviderSelection(name=self.primary_name, provider=self.primary)

        cina_provider_fallback_total.inc()
        return ProviderSelection(
            name=self.fallback_name,
            provider=self.fallback,
            fallback_triggered=True,
        )

    async def select_fallback(self) -> ProviderSelection:
        """Select fallback when available, otherwise return primary."""
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
        """Propagate successful provider outcome to circuit breaker."""
        await self.breaker.record_success(provider_name)

    async def record_failure(self, provider_name: str) -> None:
        """Propagate failed provider outcome to circuit breaker."""
        await self.breaker.record_failure(provider_name)
