"""Request schema models for query endpoint payload validation."""

from pydantic import BaseModel, Field


class QueryConfig(BaseModel):
    """Optional query-time overrides for provider and generation behavior."""

    provider: str | None = None
    max_sources: int = Field(default=10, ge=1, le=20)
    stream: bool = True
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)


class QueryRequest(BaseModel):
    """Query endpoint request body containing prompt text and options."""

    query: str = Field(min_length=1, max_length=2000)
    config: QueryConfig = Field(default_factory=QueryConfig)
