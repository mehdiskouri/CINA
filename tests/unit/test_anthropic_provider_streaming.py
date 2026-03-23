from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Self

import httpx
import pytest

from cina.models.provider import CompletionConfig, Message
from cina.orchestration.providers.anthropic import AnthropicProvider
from cina.orchestration.providers.protocol import ProviderRateLimitError, ProviderServerError


class FakeStreamResponse:
    def __init__(self, *, status_code: int, lines: list[str], body: bytes = b"error") -> None:
        self.status_code = status_code
        self._lines = lines
        self._body = body

    async def aread(self) -> bytes:
        return self._body

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class FakeStreamContext:
    def __init__(self, response: FakeStreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeStreamResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


class FakeAsyncClient:
    def __init__(self, *, timeout: httpx.Timeout) -> None:
        _ = timeout
        self.response = FakeStreamResponse(status_code=200, lines=[])
        self.post_status = 200

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def stream(self, _method: str, _url: str, **_kwargs) -> FakeStreamContext:
        return FakeStreamContext(self.response)

    async def post(self, _url: str, **_kwargs):
        return type("Resp", (), {"status_code": self.post_status})


@pytest.mark.asyncio
async def test_anthropic_complete_streams_content_block_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeAsyncClient(timeout=httpx.Timeout(5.0))
    client.response = FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"content_block_delta","delta":{"text":"Hello"}}',
            'data: {"type":"content_block_delta","delta":{"text":" world"}}',
            'data: {"type":"message_stop"}',
        ],
    )
    monkeypatch.setattr(
        "cina.orchestration.providers.anthropic.httpx.AsyncClient",
        lambda **kwargs: client,
    )

    provider = AnthropicProvider(model="claude-sonnet-4")

    chunks = [
        chunk.text
        async for chunk in provider.complete(
            [Message(role="system", content="sys"), Message(role="user", content="hi")],
            CompletionConfig(),
        )
    ]

    assert chunks == ["Hello", " world"]


@pytest.mark.asyncio
async def test_anthropic_complete_raises_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeAsyncClient(timeout=httpx.Timeout(5.0))
    client.response = FakeStreamResponse(status_code=429, lines=[])
    monkeypatch.setattr(
        "cina.orchestration.providers.anthropic.httpx.AsyncClient",
        lambda **kwargs: client,
    )

    provider = AnthropicProvider(model="claude-sonnet-4")

    with pytest.raises(ProviderRateLimitError):
        _ = [
            chunk.text
            async for chunk in provider.complete(
                [Message(role="user", content="hi")],
                CompletionConfig(),
            )
        ]


@pytest.mark.asyncio
async def test_anthropic_complete_raises_server_error_for_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeAsyncClient(timeout=httpx.Timeout(5.0))
    client.response = FakeStreamResponse(status_code=400, lines=[], body=b"invalid")
    monkeypatch.setattr(
        "cina.orchestration.providers.anthropic.httpx.AsyncClient",
        lambda **kwargs: client,
    )

    provider = AnthropicProvider(model="claude-sonnet-4")

    with pytest.raises(ProviderServerError):
        _ = [
            chunk.text
            async for chunk in provider.complete(
                [Message(role="user", content="hi")],
                CompletionConfig(),
            )
        ]


def test_anthropic_estimate_cost() -> None:
    provider = AnthropicProvider(model="claude-sonnet-4")
    assert provider.estimate_cost(1_000_000, 1_000_000) == 18.0
