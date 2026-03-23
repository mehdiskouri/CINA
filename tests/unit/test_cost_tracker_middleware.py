from __future__ import annotations

import pytest

from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.orchestration.limits.cost_tracker import CostTracker, build_cost_tracking_middleware


class FakeCostRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def insert(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_cost_tracker_log_event_persists_payload() -> None:
    repo = FakeCostRepo()
    tracker = CostTracker(repo)

    await tracker.log_event(
        query_id="q1",
        tenant_id="t1",
        provider="openai",
        model="gpt-4o",
        input_tokens=10,
        output_tokens=2,
        estimated_cost_usd=0.003,
        cache_hit=False,
    )

    assert len(repo.calls) == 1
    assert repo.calls[0]["provider"] == "openai"


def test_estimate_tokens_has_minimum_one() -> None:
    tracker = CostTracker(FakeCostRepo())
    assert tracker.estimate_tokens("") == 1
    assert tracker.estimate_tokens("abcd") == 1
    assert tracker.estimate_tokens("abcdefgh") == 2


@pytest.mark.asyncio
async def test_cost_tracking_middleware_sets_metadata_fields() -> None:
    tracker = CostTracker(FakeCostRepo())
    middleware = build_cost_tracking_middleware(tracker)

    async def _next(_messages: list[Message], _config: CompletionConfig):
        yield StreamChunk(text="hello")
        yield StreamChunk(text=" world")

    config = CompletionConfig(
        metadata={
            "provider_used": "anthropic",
            "provider_model": "claude",
            "query_id": "qid-1",
            "tenant_id": "tenant-1",
            "cache_hit": True,
            "input_tokens": "12",
            "estimate_cost": lambda inp, out: (inp + out) / 1000,
        }
    )

    chunks = [c.text async for c in middleware([Message(role="user", content="q")], config, _next)]

    assert chunks == ["hello", " world"]
    assert int(config.metadata["output_tokens"]) >= 1
    assert float(config.metadata["estimated_cost_usd"]) > 0
    cost_event = config.metadata["cost_event"]
    assert isinstance(cost_event, dict)
    assert cost_event["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_cost_tracking_middleware_without_estimator_sets_zero_cost() -> None:
    tracker = CostTracker(FakeCostRepo())
    middleware = build_cost_tracking_middleware(tracker)

    async def _next(_messages: list[Message], _config: CompletionConfig):
        yield StreamChunk(text="x")

    config = CompletionConfig(metadata={"query_id": "q", "input_tokens": object()})
    _ = [c.text async for c in middleware([Message(role="user", content="q")], config, _next)]

    assert config.metadata["estimated_cost_usd"] == 0.0
