"""Concurrent timeout fallback orchestration for provider streaming."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.orchestration.providers.protocol import ProviderError
from cina.orchestration.routing.provider_router import ProviderRouter


@dataclass(slots=True)
class FallbackStreamResult:
    provider_name: str
    fallback_triggered: bool
    stream: AsyncIterator[StreamChunk]


async def _safe_aclose(iterator: AsyncIterator[StreamChunk]) -> None:
    close_fn = getattr(iterator, "aclose", None)
    if close_fn is None:
        return
    try:
        await close_fn()
    except Exception:
        return


class ConcurrentFallbackExecutor:
    def __init__(self, router: ProviderRouter, ttft_threshold_seconds: float) -> None:
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
        primary = await self.router.select_primary()
        if primary.name != self.router.primary_name:
            return FallbackStreamResult(
                provider_name=primary.name,
                fallback_triggered=True,
                stream=primary.provider.complete(messages, config),
            )

        primary_iter = primary.provider.complete(messages, config)
        primary_task: asyncio.Task[StreamChunk] = asyncio.create_task(
            self._next_chunk(primary_iter)
        )
        try:
            first_chunk = await asyncio.wait_for(
                asyncio.shield(primary_task),
                timeout=self.ttft_threshold_seconds,
            )
            await self.router.record_success(primary.name)
            return FallbackStreamResult(
                provider_name=primary.name,
                fallback_triggered=False,
                stream=self._stream_with_first(primary_iter, first_chunk),
            )
        except TimeoutError:
            fallback = await self.router.select_fallback()
            fallback_iter = fallback.provider.complete(messages, config)
            fallback_task: asyncio.Task[StreamChunk] = asyncio.create_task(
                self._next_chunk(fallback_iter)
            )

            done, pending = await asyncio.wait(
                {primary_task, fallback_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            winner: asyncio.Task[Any] = done.pop()

            for task in pending:
                task.cancel()

            if winner is primary_task:
                try:
                    chunk = winner.result()
                    await self.router.record_success(primary.name)
                    await _safe_aclose(fallback_iter)
                    return FallbackStreamResult(
                        provider_name=primary.name,
                        fallback_triggered=False,
                        stream=self._stream_with_first(primary_iter, chunk),
                    )
                except Exception as exc:
                    await self.router.record_failure(primary.name)
                    if isinstance(exc, StopAsyncIteration):
                        return FallbackStreamResult(
                            provider_name=primary.name,
                            fallback_triggered=False,
                            stream=self._empty_stream(),
                        )
                    # If primary failed while racing, use fallback path directly.
                    return FallbackStreamResult(
                        provider_name=fallback.name,
                        fallback_triggered=True,
                        stream=fallback_iter,
                    )

            await self.router.record_failure(primary.name)
            try:
                chunk = winner.result()
                await self.router.record_success(fallback.name)
                await _safe_aclose(primary_iter)
                return FallbackStreamResult(
                    provider_name=fallback.name,
                    fallback_triggered=True,
                    stream=self._stream_with_first(fallback_iter, chunk),
                )
            except Exception as exc:
                await self.router.record_failure(fallback.name)
                if isinstance(exc, StopAsyncIteration):
                    return FallbackStreamResult(
                        provider_name=fallback.name,
                        fallback_triggered=True,
                        stream=self._empty_stream(),
                    )
                if isinstance(exc, ProviderError):
                    raise
                raise
        except StopAsyncIteration:
            await self.router.record_success(primary.name)
            return FallbackStreamResult(
                provider_name=primary.name,
                fallback_triggered=False,
                stream=self._empty_stream(),
            )
        except Exception:
            await self.router.record_failure(primary.name)
            fallback = await self.router.select_fallback()
            return FallbackStreamResult(
                provider_name=fallback.name,
                fallback_triggered=True,
                stream=fallback.provider.complete(messages, config),
            )

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
