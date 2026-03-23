"""Anthropic streaming LLM provider — minimal Phase 2 implementation."""

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

log = get_logger("cina.orchestration.providers.anthropic")

_API_BASE = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_STATUS_OK = 200
_STATUS_RATE_LIMIT = 429
_STATUS_SERVER_MIN = 500
_STATUS_SERVER_MAX = 600
_HEALTH_TIMEOUT_SECONDS = 10.0
_ERROR_LOG_PREVIEW_CHARS = 500
_ERROR_PREVIEW_CHARS = 200

# Sonnet pricing per 1M tokens (as of 2025)
_INPUT_PRICE_PER_M = 3.0
_OUTPUT_PRICE_PER_M = 15.0

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class AnthropicProvider:
    """Streaming Anthropic Messages API provider.

    Phase 2: no fallback, no circuit breaker — just enough to get tokens flowing.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "ANTHROPIC_API_KEY",
        timeout_connect: float = 5.0,
        timeout_read: float = 60.0,
    ) -> None:
        """Initialize Anthropic provider configuration and request timeouts."""
        self.name = "anthropic"
        self.model = model
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            log.warning("anthropic_api_key_missing", env_var=api_key_env)
        self._api_key = api_key
        self._timeout = httpx.Timeout(
            connect=timeout_connect,
            read=timeout_read,
            write=10.0,
            pool=10.0,
        )

    @staticmethod
    def _split_system_and_user_messages(
        messages: list[Message],
    ) -> tuple[str, list[dict[str, str]]]:
        system_text = ""
        user_messages: list[dict[str, str]] = []
        for msg in messages:
            if msg.role == "system":
                system_text = msg.content
            else:
                user_messages.append({"role": msg.role, "content": msg.content})
        return system_text, user_messages

    def _build_payload(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> dict[str, object]:
        system_text, user_messages = self._split_system_and_user_messages(messages)
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": True,
            "messages": user_messages,
        }
        if system_text:
            payload["system"] = system_text
        return payload

    def _build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

    async def _raise_for_status(self, response: httpx.Response) -> None:
        status_code = response.status_code
        if status_code == _STATUS_RATE_LIMIT:
            message = "Anthropic rate limit"
            raise ProviderRateLimitError(message, provider=self.name)
        if _STATUS_SERVER_MIN <= status_code < _STATUS_SERVER_MAX:
            message = f"Anthropic server error {status_code}"
            raise ProviderServerError(message, provider=self.name)
        if status_code != _STATUS_OK:
            body = (await response.aread()).decode("utf-8", errors="ignore")
            log.error(
                "anthropic_api_error",
                status_code=status_code,
                body=body[:_ERROR_LOG_PREVIEW_CHARS],
            )
            message = f"Anthropic API error {status_code}: {body[:_ERROR_PREVIEW_CHARS]}"
            raise ProviderServerError(message, provider=self.name)

    @staticmethod
    def _extract_event_text(raw_line: str) -> str:
        if not raw_line.startswith("data: "):
            return ""
        payload = raw_line[6:]
        if payload.strip() == "[DONE]":
            return ""

        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return ""

        event_type = event.get("type", "")
        if event_type == "message_stop":
            return ""
        if event_type != "content_block_delta":
            return ""
        delta = event.get("delta", {})
        return str(delta.get("text", ""))

    async def _stream_response(self, response: httpx.Response) -> AsyncIterator[StreamChunk]:
        async for line in response.aiter_lines():
            text = self._extract_event_text(line)
            if text:
                yield StreamChunk(text=text)

    async def complete(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> AsyncIterator[StreamChunk]:
        """Stream completion tokens from the Anthropic Messages API."""
        payload = self._build_payload(messages, config)
        headers = self._build_headers()

        start = time.perf_counter()
        status = "success"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream("POST", _API_BASE, json=payload, headers=headers) as resp:
                    await self._raise_for_status(resp)
                    async for chunk in self._stream_response(resp):
                        yield chunk
            except httpx.TimeoutException as exc:
                status = "error"
                log.exception("anthropic_timeout", error=str(exc))
                raise ProviderTimeoutError(str(exc), provider=self.name) from exc
            except httpx.HTTPError as exc:
                status = "error"
                log.exception("anthropic_http_error", error=str(exc))
                raise ProviderServerError(str(exc), provider=self.name) from exc
            finally:
                elapsed = time.perf_counter() - start
                cina_provider_latency_seconds.labels(provider=self.name).observe(elapsed)
                cina_provider_request_total.labels(provider=self.name, status=status).inc()

    async def health_check(self) -> bool:
        """Quick connectivity check — send a minimal request."""
        try:
            headers = self._build_headers()
            payload = {
                "model": self.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(_HEALTH_TIMEOUT_SECONDS)) as client:
                resp = await client.post(_API_BASE, json=payload, headers=headers)
                return resp.status_code == _STATUS_OK
        except (httpx.TimeoutException, httpx.HTTPError):
            return False

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate Anthropic request cost in USD."""
        return (input_tokens / 1_000_000 * _INPUT_PRICE_PER_M) + (
            output_tokens / 1_000_000 * _OUTPUT_PRICE_PER_M
        )
