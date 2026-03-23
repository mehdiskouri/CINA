"""OpenAI streaming LLM provider."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

import httpx

from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.observability.logging import get_logger
from cina.observability.metrics import cina_provider_latency_seconds, cina_provider_request_total
from cina.orchestration.providers.protocol import (
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)

log = get_logger("cina.orchestration.providers.openai")

_API_BASE = "https://api.openai.com/v1/chat/completions"
_STATUS_OK = 200
_STATUS_BAD_REQUEST = 400
_STATUS_RATE_LIMIT = 429
_STATUS_SERVER_MIN = 500
_STATUS_SERVER_MAX = 600
_HEALTH_TIMEOUT_SECONDS = 10.0
_ERROR_PREVIEW_CHARS = 200

# GPT-4o pricing per 1M tokens
_INPUT_PRICE_PER_M = 5.0
_OUTPUT_PRICE_PER_M = 15.0

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class OpenAIProvider:
    """Streaming provider adapter for OpenAI chat completions API."""

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_connect: float = 5.0,
        timeout_read: float = 60.0,
    ) -> None:
        """Initialize OpenAI provider configuration and request timeouts."""
        self.name = "openai"
        self.model = model
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            log.warning("openai_api_key_missing", env_var=api_key_env)
        self._api_key = api_key
        self._timeout = httpx.Timeout(
            connect=timeout_connect,
            read=timeout_read,
            write=10.0,
            pool=10.0,
        )

    @staticmethod
    def _extract_delta_text(raw_line: str) -> str:
        if not raw_line.startswith("data: "):
            return ""
        raw = raw_line[6:].strip()
        if raw == "[DONE]":
            return ""
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        choices = event.get("choices", [])
        if not choices:
            return ""
        delta = choices[0].get("delta", {})
        return str(delta.get("content", ""))

    def _build_payload(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> dict[str, object]:
        return {
            "model": self.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def _raise_for_status(self, response: httpx.Response) -> None:
        status_code = response.status_code
        if status_code == _STATUS_RATE_LIMIT:
            message = "OpenAI rate limit"
            raise ProviderRateLimitError(message, provider=self.name)
        if _STATUS_SERVER_MIN <= status_code < _STATUS_SERVER_MAX:
            message = f"OpenAI server error {status_code}"
            raise ProviderServerError(message, provider=self.name)
        if status_code >= _STATUS_BAD_REQUEST:
            body = (await response.aread()).decode("utf-8", errors="ignore")
            preview = body[:_ERROR_PREVIEW_CHARS]
            message = f"OpenAI API error {status_code}: {preview}"
            raise ProviderServerError(message, provider=self.name)

    async def _stream_response(self, response: httpx.Response) -> AsyncIterator[StreamChunk]:
        async for line in response.aiter_lines():
            text = self._extract_delta_text(line)
            if text:
                yield StreamChunk(text=text)

    async def complete(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> AsyncIterator[StreamChunk]:
        """Stream completion tokens from the OpenAI API."""
        payload = self._build_payload(messages, config)
        headers = self._build_headers()

        status = "success"
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream("POST", _API_BASE, json=payload, headers=headers) as resp:
                    await self._raise_for_status(resp)
                    async for chunk in self._stream_response(resp):
                        yield chunk
            except httpx.TimeoutException as exc:
                status = "error"
                raise ProviderTimeoutError(str(exc), provider=self.name) from exc
            except httpx.HTTPError as exc:
                status = "error"
                raise ProviderServerError(str(exc), provider=self.name) from exc
            finally:
                elapsed = time.perf_counter() - start
                cina_provider_latency_seconds.labels(provider=self.name).observe(elapsed)
                cina_provider_request_total.labels(provider=self.name, status=status).inc()

    async def health_check(self) -> bool:
        """Send a minimal request to validate OpenAI connectivity."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(_HEALTH_TIMEOUT_SECONDS)) as client:
                resp = await client.post(_API_BASE, json=payload, headers=headers)
                return resp.status_code == _STATUS_OK
        except (httpx.TimeoutException, httpx.HTTPError):
            return False

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate OpenAI request cost in USD."""
        return (input_tokens / 1_000_000 * _INPUT_PRICE_PER_M) + (
            output_tokens / 1_000_000 * _OUTPUT_PRICE_PER_M
        )
