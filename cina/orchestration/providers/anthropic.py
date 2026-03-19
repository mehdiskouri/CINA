"""Anthropic streaming LLM provider — minimal Phase 2 implementation."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx

from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.observability.logging import get_logger
from cina.observability.metrics import cina_provider_latency_seconds, cina_provider_request_total

log = get_logger("cina.orchestration.providers.anthropic")

_API_BASE = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"

# Sonnet pricing per 1M tokens (as of 2025)
_INPUT_PRICE_PER_M = 3.0
_OUTPUT_PRICE_PER_M = 15.0


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
        self.model = model
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            log.warning("anthropic_api_key_missing", env_var=api_key_env)
        self._api_key = api_key
        self._timeout = httpx.Timeout(
            connect=timeout_connect, read=timeout_read, write=10.0, pool=10.0
        )

    async def complete(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> AsyncIterator[StreamChunk]:
        """Stream completion tokens from the Anthropic Messages API."""
        # Anthropic API expects system as a top-level param, not in messages
        system_text = ""
        user_messages: list[dict[str, str]] = []
        for msg in messages:
            if msg.role == "system":
                system_text = msg.content
            else:
                user_messages.append({"role": msg.role, "content": msg.content})

        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": True,
            "messages": user_messages,
        }
        if system_text:
            payload["system"] = system_text

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

        import time

        start = time.perf_counter()
        status = "success"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream("POST", _API_BASE, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        status = "error"
                        log.error(
                            "anthropic_api_error",
                            status_code=resp.status_code,
                            body=body.decode()[:500],
                        )
                        raise RuntimeError(
                            f"Anthropic API error {resp.status_code}: {body.decode()[:200]}"
                        )

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break

                        import json

                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        event_type = event.get("type", "")
                        if event_type == "content_block_delta":
                            delta = event.get("delta", {})
                            text = delta.get("text", "")
                            if text:
                                yield StreamChunk(text=text)
                        elif event_type == "message_stop":
                            break
            except httpx.HTTPError as exc:
                status = "error"
                log.error("anthropic_http_error", error=str(exc))
                raise
            finally:
                elapsed = time.perf_counter() - start
                cina_provider_latency_seconds.labels(provider="anthropic").observe(elapsed)
                cina_provider_request_total.labels(provider="anthropic", status=status).inc()

    async def health_check(self) -> bool:
        """Quick connectivity check — send a minimal request."""
        try:
            headers = {
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            }
            payload = {
                "model": self.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.post(_API_BASE, json=payload, headers=headers)
                return resp.status_code == 200
        except Exception:
            return False

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens / 1_000_000 * _INPUT_PRICE_PER_M) + (
            output_tokens / 1_000_000 * _OUTPUT_PRICE_PER_M
        )
