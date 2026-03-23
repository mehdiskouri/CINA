"""Models for persisted query and cost tracking records."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class QueryLog:
    """Persisted query execution summary."""

    id: UUID
    query_text: str
    provider_used: str
    created_at: datetime


@dataclass(slots=True)
class CostEvent:
    """Per-query token and cost accounting event."""

    id: UUID
    query_id: UUID
    tenant_id: str | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
