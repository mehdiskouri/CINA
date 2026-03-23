from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Self

import httpx
import pytest

from cina.models.provider import CompletionConfig, Message
from cina.orchestration.providers.openai import OpenAIProvider
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
        self.raise_in_post = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def stream(self, _method: str, _url: str, **_kwargs) -> FakeStreamContext:
        return FakeStreamContext(self.response)

    async def post(self, _url: str, **_kwargs):
        if self.raise_in_post:
            raise httpx.HTTPError("boom")
        return type("Resp", (), {"status_code": self.post_status})


@pytest.mark.asyncio
async def test_openai_complete_streams_text_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeAsyncClient(timeout=httpx.Timeout(5.0))
    client.response = FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ],
    )
    monkeypatch.setattr(
        "cina.orchestration.providers.openai.httpx.AsyncClient", lambda **kwargs: client
    )

    provider = OpenAIProvider(model="gpt-4o")
    chunks = [
        chunk.text
        async for chunk in provider.complete(
            [Message(role="user", content="hi")],
            CompletionConfig(max_tokens=32, temperature=0.2),
        )
    ]

    assert chunks == ["Hello", " world"]


@pytest.mark.asyncio
async def test_openai_complete_raises_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeAsyncClient(timeout=httpx.Timeout(5.0))
    client.response = FakeStreamResponse(status_code=429, lines=[])
    monkeypatch.setattr(
        "cina.orchestration.providers.openai.httpx.AsyncClient", lambda **kwargs: client
    )

    provider = OpenAIProvider(model="gpt-4o")

    with pytest.raises(ProviderRateLimitError):
        _ = [
            chunk.text
            async for chunk in provider.complete(
                [Message(role="user", content="hi")],
                CompletionConfig(),
            )
        ]


@pytest.mark.asyncio
async def test_openai_complete_raises_server_error_for_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeAsyncClient(timeout=httpx.Timeout(5.0))
    client.response = FakeStreamResponse(status_code=400, lines=[], body=b"bad request")
    monkeypatch.setattr(
        "cina.orchestration.providers.openai.httpx.AsyncClient", lambda **kwargs: client
    )

    provider = OpenAIProvider(model="gpt-4o")

    with pytest.raises(ProviderServerError):
        _ = [
            chunk.text
            async for chunk in provider.complete(
                [Message(role="user", content="hi")],
                CompletionConfig(),
            )
        ]


@pytest.mark.asyncio
async def test_openai_health_check_false_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeAsyncClient(timeout=httpx.Timeout(5.0))
    client.raise_in_post = True
    monkeypatch.setattr(
        "cina.orchestration.providers.openai.httpx.AsyncClient", lambda **kwargs: client
    )

    provider = OpenAIProvider(model="gpt-4o")

    ok = await provider.health_check()
    assert ok is False


def test_openai_estimate_cost() -> None:
    provider = OpenAIProvider(model="gpt-4o")
    assert provider.estimate_cost(1_000_000, 1_000_000) == 20.0
