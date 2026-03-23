"""OpenAI embedding provider with retries and token-bucket pacing."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import tiktoken
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

from cina.config import load_config

_RETRYABLE_STATUS_CODE = 500
_RETRY_JITTER_SECONDS = 0.25


@dataclass(slots=True)
class _TokenBucket:
    """Simple token bucket limiter measured in tokens per minute."""

    rate_tpm: int
    capacity: int
    tokens: float
    last_refill: float

    def consume(self, amount: int) -> float:
        """Consume requested tokens and return wait duration if throttled."""
        now = time.monotonic()
        elapsed = max(0.0, now - self.last_refill)
        refill_per_second = self.rate_tpm / 60.0
        self.tokens = min(self.capacity, self.tokens + elapsed * refill_per_second)
        self.last_refill = now
        if self.tokens >= amount:
            self.tokens -= amount
            return 0.0
        deficit = amount - self.tokens
        wait_seconds = deficit / refill_per_second if refill_per_second > 0 else 0.0
        self.tokens = 0.0
        return max(0.0, wait_seconds)


class OpenAIEmbeddingProvider:
    """Embedding provider implementation backed by OpenAI API."""

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize provider from loaded application configuration."""
        cfg = load_config().ingestion.embedding
        self.model = cfg.model
        self.dimensions = cfg.dimensions
        self.max_retries = cfg.max_retries
        self.client = AsyncOpenAI(api_key=api_key)
        self.encoder = tiktoken.get_encoding(load_config().ingestion.chunk.tokenizer)
        self.bucket = _TokenBucket(
            rate_tpm=cfg.rate_limit_tpm,
            capacity=cfg.rate_limit_tpm,
            tokens=float(cfg.rate_limit_tpm),
            last_refill=time.monotonic(),
        )

    async def embed(self, texts: list[str], model: str, dimensions: int) -> list[list[float]]:
        """Embed a batch of texts with retry/backoff on transient failures."""
        token_estimate = sum(len(self.encoder.encode(text)) for text in texts)
        wait_seconds = self.bucket.consume(max(1, token_estimate))
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.embeddings.create(
                    model=model,
                    input=texts,
                    dimensions=dimensions,
                )
                return [item.embedding for item in response.data]
            except (RateLimitError, APIConnectionError, APIStatusError) as exc:
                retryable = isinstance(exc, (RateLimitError, APIConnectionError)) or (
                    isinstance(exc, APIStatusError) and exc.status_code >= _RETRYABLE_STATUS_CODE
                )
                if attempt >= self.max_retries or not retryable:
                    raise
                backoff = min(30.0, (2**attempt) + _RETRY_JITTER_SECONDS)
                await asyncio.sleep(backoff)
        message = "Embedding retries exhausted"
        raise RuntimeError(message)

    async def health_check(self) -> bool:
        """Return provider health by performing a tiny embedding request."""
        try:
            _ = await self.embed(["healthcheck"], model=self.model, dimensions=self.dimensions)
        except (RuntimeError, ValueError, TypeError, APIConnectionError, APIStatusError):
            return False
        else:
            return True
