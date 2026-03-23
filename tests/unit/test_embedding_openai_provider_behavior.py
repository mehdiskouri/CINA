from __future__ import annotations

import asyncio

import pytest

from cina.ingestion.embedding.openai import OpenAIEmbeddingProvider, _TokenBucket


class FakeEncoder:
    def encode(self, text: str) -> list[int]:
        return [1] * max(1, len(text) // 3)


class FakeEmbeddingsAPI:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses

    async def create(self, *, model: str, input: list[str], dimensions: int):
        _ = (model, input, dimensions)
        current = self.responses.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.embeddings = FakeEmbeddingsAPI(responses)


class _DataItem:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding


class _Response:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_DataItem(v) for v in vectors]


class FakeRateLimitError(Exception):
    pass


class FakeAPIConnectionError(Exception):
    pass


def _provider_for_test(responses: list[object], *, max_retries: int = 2) -> OpenAIEmbeddingProvider:
    provider = OpenAIEmbeddingProvider.__new__(OpenAIEmbeddingProvider)
    provider.model = "m"
    provider.dimensions = 4
    provider.max_retries = max_retries
    provider.client = FakeClient(responses)
    provider.encoder = FakeEncoder()
    provider.bucket = _TokenBucket(rate_tpm=1000, capacity=1000, tokens=1000.0, last_refill=0.0)
    return provider


def test_token_bucket_consume_paths() -> None:
    bucket = _TokenBucket(rate_tpm=600, capacity=600, tokens=1.0, last_refill=0.0)
    wait = bucket.consume(10)
    assert wait >= 0


@pytest.mark.asyncio
async def test_embed_success_returns_vectors() -> None:
    provider = _provider_for_test([_Response([[0.1, 0.2], [0.3, 0.4]])])
    out = await provider.embed(["a", "b"], model="m", dimensions=2)
    assert out == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.asyncio
async def test_embed_retries_then_succeeds() -> None:
    provider = _provider_for_test(
        [
            FakeRateLimitError("rate"),
            FakeAPIConnectionError("conn"),
            _Response([[0.9, 0.8]]),
        ],
        max_retries=3,
    )

    # Replace caught exception types on provider module-level names without pytest monkeypatch.
    import cina.ingestion.embedding.openai as openai_module

    original_rate = openai_module.RateLimitError
    original_conn = openai_module.APIConnectionError
    try:
        openai_module.RateLimitError = FakeRateLimitError
        openai_module.APIConnectionError = FakeAPIConnectionError
        out = await provider.embed(["abc"], model="m", dimensions=2)
    finally:
        openai_module.RateLimitError = original_rate
        openai_module.APIConnectionError = original_conn

    assert out == [[0.9, 0.8]]


@pytest.mark.asyncio
async def test_health_check_false_when_embed_raises() -> None:
    provider = _provider_for_test([RuntimeError("boom")], max_retries=0)
    ok = await provider.health_check()
    assert ok is False


@pytest.mark.asyncio
async def test_embed_exhausts_retries_raises_last_error() -> None:
    provider = _provider_for_test([FakeRateLimitError("rate")], max_retries=0)

    import cina.ingestion.embedding.openai as openai_module

    original_rate = openai_module.RateLimitError
    try:
        openai_module.RateLimitError = FakeRateLimitError
        with pytest.raises(FakeRateLimitError):
            await provider.embed(["abc"], model="m", dimensions=2)
    finally:
        openai_module.RateLimitError = original_rate


@pytest.mark.asyncio
async def test_embed_waits_when_bucket_requires_sleep() -> None:
    provider = _provider_for_test([_Response([[0.1]])])
    provider.bucket.tokens = 0.0
    provider.bucket.last_refill = asyncio.get_event_loop().time()

    out = await provider.embed(["long text"], model="m", dimensions=1)
    assert out == [[0.1]]
