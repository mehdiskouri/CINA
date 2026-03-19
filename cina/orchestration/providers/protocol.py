from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from cina.models.provider import CompletionConfig, Message, StreamChunk


class LLMProviderProtocol(Protocol):
    async def complete(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> AsyncIterator[StreamChunk]: ...

    async def health_check(self) -> bool: ...

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float: ...
