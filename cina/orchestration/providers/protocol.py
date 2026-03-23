"""Provider protocol and provider-specific exception types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cina.models.provider import CompletionConfig, Message, StreamChunk


class ProviderError(RuntimeError):
    """Base error for provider-level failures."""

    def __init__(self, message: str, *, provider: str) -> None:
        """Initialize a provider-scoped error message."""
        super().__init__(message)
        self.provider = provider


class ProviderTimeoutError(ProviderError):
    """Provider request timed out."""


class ProviderRateLimitError(ProviderError):
    """Provider returned a rate-limit response."""


class ProviderServerError(ProviderError):
    """Provider returned a transient server error."""


class LLMProviderProtocol(Protocol):
    """Contract implemented by all streaming LLM provider adapters."""

    def complete(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> AsyncIterator[StreamChunk]:
        """Start a streaming completion for the provided message list."""

    async def health_check(self) -> bool:
        """Return whether provider connectivity appears healthy."""

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate request cost in USD from token counts."""
