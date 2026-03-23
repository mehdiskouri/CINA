from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from cina.models.query import CostEvent, QueryLog


def test_query_log_model_fields() -> None:
    item = QueryLog(
        id=uuid4(),
        query_text="What is trastuzumab?",
        provider_used="anthropic",
        created_at=datetime.now(UTC),
    )

    assert item.query_text.startswith("What")
    assert item.provider_used == "anthropic"


def test_cost_event_model_fields() -> None:
    event = CostEvent(
        id=uuid4(),
        query_id=uuid4(),
        tenant_id="tenant-a",
        provider="openai",
        model="gpt-4o",
        input_tokens=123,
        output_tokens=45,
        estimated_cost_usd=0.012,
    )

    assert event.input_tokens == 123
    assert event.output_tokens == 45
    assert event.estimated_cost_usd > 0
