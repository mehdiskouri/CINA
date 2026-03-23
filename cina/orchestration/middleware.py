"""Composable middleware types and composition helper for provider handlers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from cina.models.provider import CompletionConfig, Message, StreamChunk

Handler = Callable[[list[Message], CompletionConfig], AsyncIterator[StreamChunk]]
Middleware = Callable[[list[Message], CompletionConfig, Handler], AsyncIterator[StreamChunk]]


def compose(*middlewares: Middleware) -> Callable[[Handler], Handler]:
    """Compose middlewares around a final handler in declaration order."""

    def _composer(final_handler: Handler) -> Handler:
        handler = final_handler
        for middleware in reversed(middlewares):
            next_handler = handler

            def _wrapped(
                messages: list[Message],
                config: CompletionConfig,
                mw: Middleware = middleware,
                nxt: Handler = next_handler,
            ) -> AsyncIterator[StreamChunk]:
                return mw(messages, config, nxt)

            handler = _wrapped
        return handler

    return _composer
