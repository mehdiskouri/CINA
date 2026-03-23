"""Cost tracking for LLM calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cina.observability.metrics import cina_cost_usd_total

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cina.db.repositories.cost_event import CostEventInsert, CostEventRepository
    from cina.models.provider import CompletionConfig, Message, StreamChunk
    from cina.orchestration.middleware import Handler, Middleware


class CostTracker:
    """Tracks and persists per-query cost events."""

    def __init__(self, repository: CostEventRepository) -> None:
        """Initialize tracker with the cost-event repository."""
        self.repository = repository

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count with a lightweight character heuristic."""
        # Lightweight approximation to avoid adding heavy tokenizer cost in hot path.
        return max(1, len(text) // 4)

    async def log_event(self, event: CostEventInsert) -> None:
        """Record metrics and persist a cost event payload."""
        cina_cost_usd_total.labels(
            provider=event.provider,
            tenant=event.tenant_id or "unknown",
        ).inc(event.estimated_cost_usd)
        await self.repository.insert(event)


def build_cost_tracking_middleware(cost_tracker: CostTracker) -> Middleware:
    """Build middleware that estimates and attaches request cost metadata."""

    async def middleware(
        messages: list[Message],
        config: CompletionConfig,
        next_handler: Handler,
    ) -> AsyncIterator[StreamChunk]:
        output_parts: list[str] = []
        async for chunk in next_handler(messages, config):
            output_parts.append(chunk.text)
            yield chunk

        provider_name = str(config.metadata.get("provider_used", "unknown"))
        provider_model = str(config.metadata.get("provider_model", "unknown"))
        query_id = str(config.metadata.get("query_id", "00000000-0000-0000-0000-000000000000"))
        tenant_id = config.metadata.get("tenant_id")
        cache_hit = bool(config.metadata.get("cache_hit", False))

        input_tokens_raw = config.metadata.get("input_tokens", 0)
        if isinstance(input_tokens_raw, (int, float, str)):
            input_tokens = int(input_tokens_raw)
        else:
            input_tokens = 0
        output_tokens = cost_tracker.estimate_tokens("".join(output_parts))
        estimate_fn = config.metadata.get("estimate_cost")
        if callable(estimate_fn):
            estimated_cost_usd = float(estimate_fn(input_tokens, output_tokens))
        else:
            estimated_cost_usd = 0.0

        config.metadata["output_tokens"] = output_tokens
        config.metadata["estimated_cost_usd"] = estimated_cost_usd
        config.metadata["cost_event"] = {
            "query_id": query_id,
            "tenant_id": str(tenant_id) if isinstance(tenant_id, str) else None,
            "provider": provider_name,
            "model": provider_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost_usd,
            "cache_hit": cache_hit,
        }

    return middleware
