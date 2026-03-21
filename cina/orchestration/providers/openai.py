"""OpenAI streaming LLM provider."""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator

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

# GPT-4o pricing per 1M tokens
_INPUT_PRICE_PER_M = 5.0
_OUTPUT_PRICE_PER_M = 15.0


class OpenAIProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_connect: float = 5.0,
        timeout_read: float = 60.0,
    ) -> None:
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

    async def complete(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": self.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        status = "success"
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream("POST", _API_BASE, json=payload, headers=headers) as resp:
                    if resp.status_code == 429:
                        status = "error"
                        raise ProviderRateLimitError("OpenAI rate limit", provider=self.name)
                    if 500 <= resp.status_code < 600:
                        status = "error"
                        raise ProviderServerError(
                            f"OpenAI server error {resp.status_code}",
                            provider=self.name,
                        )
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", errors="ignore")
                        status = "error"
                        raise ProviderServerError(
                            f"OpenAI API error {resp.status_code}: {body[:200]}",
                            provider=self.name,
                        )

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        choices = event.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        text = delta.get("content", "")
                        if text:
                            yield StreamChunk(text=text)
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
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.post(_API_BASE, json=payload, headers=headers)
                return resp.status_code == 200
        except Exception:
            return False

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens / 1_000_000 * _INPUT_PRICE_PER_M) + (
            output_tokens / 1_000_000 * _OUTPUT_PRICE_PER_M
        )
