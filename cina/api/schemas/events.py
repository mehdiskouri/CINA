"""Pydantic schema models for SSE envelope payloads."""

from pydantic import BaseModel


class SSEEvent(BaseModel):
    """Represents a serialized SSE event payload."""

    event: str
    data: dict[str, object]
