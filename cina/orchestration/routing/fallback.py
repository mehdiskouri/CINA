"""Concurrent timeout fallback orchestration for provider streaming."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.orchestration.providers.protocol import ProviderError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cina.orchestration.providers.protocol import LLMProviderProtocol
    from cina.orchestration.routing.provider_router import ProviderRouter


@dataclass(slots=True)
class FallbackStreamResult:
    """Final selected provider stream with fallback metadata."""

    provider_name: str
    fallback_triggered: bool
    stream: AsyncIterator[StreamChunk]


async def _safe_aclose(iterator: AsyncIterator[StreamChunk]) -> None:
    """Best-effort close for async iterators used during races."""
    close_fn = getattr(iterator, "aclose", None)
    if close_fn is None:
        return
    try:
        await close_fn()
    except (RuntimeError, ValueError, TypeError):
        return


class ConcurrentFallbackExecutor:
    """Executes provider fallback when primary TTFT exceeds threshold."""

    def __init__(self, router: ProviderRouter, ttft_threshold_seconds: float) -> None:
        """Initialize fallback executor with provider router and TTFT budget."""
        self.router = router
        self.ttft_threshold_seconds = ttft_threshold_seconds

    @staticmethod
    async def _next_chunk(iterator: AsyncIterator[StreamChunk]) -> StreamChunk:
        return await anext(iterator)

    async def complete(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> FallbackStreamResult:
        """Return a stream from primary or fallback based on startup latency/failures."""
        primary = await self.router.select_primary()
        if primary.name != self.router.primary_name:
            return FallbackStreamResult(
                provider_name=primary.name,
                fallback_triggered=True,
                stream=primary.provider.complete(messages, config),
            )

        return await self._complete_with_primary(
            primary_name=primary.name,
            primary_provider=primary.provider,
            messages=messages,
            config=config,
        )

    async def _complete_with_primary(
        self,
        *,
        primary_name: str,
        primary_provider: LLMProviderProtocol,
        messages: list[Message],
        config: CompletionConfig,
    ) -> FallbackStreamResult:
        primary_iter = primary_provider.complete(messages, config)
        primary_task: asyncio.Task[StreamChunk] = asyncio.create_task(
            self._next_chunk(primary_iter),
        )
        try:
            first_chunk = await asyncio.wait_for(
                asyncio.shield(primary_task),
                timeout=self.ttft_threshold_seconds,
            )
            await self.router.record_success(primary_name)
            return FallbackStreamResult(
                provider_name=primary_name,
                fallback_triggered=False,
                stream=self._stream_with_first(primary_iter, first_chunk),
            )
        except TimeoutError:
            return await self._complete_race_with_fallback(
                primary_name=primary_name,
                primary_iter=primary_iter,
                primary_task=primary_task,
                messages=messages,
                config=config,
            )
        except StopAsyncIteration:
            await self.router.record_success(primary_name)
            return FallbackStreamResult(
                provider_name=primary_name,
                fallback_triggered=False,
                stream=self._empty_stream(),
            )
        except (ProviderError, RuntimeError, ValueError):
            await self.router.record_failure(primary_name)
            fallback = await self.router.select_fallback()
            return FallbackStreamResult(
                provider_name=fallback.name,
                fallback_triggered=True,
                stream=fallback.provider.complete(messages, config),
            )

    async def _complete_race_with_fallback(
        self,
        *,
        primary_name: str,
        primary_iter: AsyncIterator[StreamChunk],
        primary_task: asyncio.Task[StreamChunk],
        messages: list[Message],
        config: CompletionConfig,
    ) -> FallbackStreamResult:
        fallback = await self.router.select_fallback()
        fallback_iter = fallback.provider.complete(messages, config)
        fallback_task: asyncio.Task[StreamChunk] = asyncio.create_task(
            self._next_chunk(fallback_iter),
        )

        done, pending = await asyncio.wait(
            {primary_task, fallback_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        winner: asyncio.Task[StreamChunk] = done.pop()

        for task in pending:
            task.cancel()

        if winner is primary_task:
            return await self._handle_primary_race_winner(
                primary_name=primary_name,
                primary_iter=primary_iter,
                fallback_name=fallback.name,
                fallback_iter=fallback_iter,
                winner=winner,
            )

        return await self._handle_fallback_race_winner(
            primary_name=primary_name,
            primary_iter=primary_iter,
            fallback_name=fallback.name,
            fallback_iter=fallback_iter,
            winner=winner,
        )

    async def _handle_primary_race_winner(
        self,
        *,
        primary_name: str,
        primary_iter: AsyncIterator[StreamChunk],
        fallback_name: str,
        fallback_iter: AsyncIterator[StreamChunk],
        winner: asyncio.Task[StreamChunk],
    ) -> FallbackStreamResult:
        try:
            chunk = winner.result()
            await self.router.record_success(primary_name)
            await _safe_aclose(fallback_iter)
            return FallbackStreamResult(
                provider_name=primary_name,
                fallback_triggered=False,
                stream=self._stream_with_first(primary_iter, chunk),
            )
        except StopAsyncIteration:
            await self.router.record_failure(primary_name)
            return FallbackStreamResult(
                provider_name=primary_name,
                fallback_triggered=False,
                stream=self._empty_stream(),
            )
        except ProviderError:
            await self.router.record_failure(primary_name)
            return FallbackStreamResult(
                provider_name=fallback_name,
                fallback_triggered=True,
                stream=fallback_iter,
            )

    async def _handle_fallback_race_winner(
        self,
        *,
        primary_name: str,
        primary_iter: AsyncIterator[StreamChunk],
        fallback_name: str,
        fallback_iter: AsyncIterator[StreamChunk],
        winner: asyncio.Task[StreamChunk],
    ) -> FallbackStreamResult:
        await self.router.record_failure(primary_name)
        try:
            chunk = winner.result()
            await self.router.record_success(fallback_name)
            await _safe_aclose(primary_iter)
            return FallbackStreamResult(
                provider_name=fallback_name,
                fallback_triggered=True,
                stream=self._stream_with_first(fallback_iter, chunk),
            )
        except StopAsyncIteration:
            await self.router.record_failure(fallback_name)
            return FallbackStreamResult(
                provider_name=fallback_name,
                fallback_triggered=True,
                stream=self._empty_stream(),
            )
        except ProviderError:
            await self.router.record_failure(fallback_name)
            raise

    async def _empty_stream(self) -> AsyncIterator[StreamChunk]:
        for _ in ():
            yield StreamChunk(text="")

    async def _stream_with_first(
        self,
        iterator: AsyncIterator[StreamChunk],
        first: StreamChunk,
    ) -> AsyncIterator[StreamChunk]:
        yield first
        async for chunk in iterator:
            yield chunk
