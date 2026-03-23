from __future__ import annotations

from cina.api.schemas.events import SSEEvent


def test_sse_event_schema_fields() -> None:
    event = SSEEvent(event="token", data={"text": "hello"})

    assert event.event == "token"
    assert event.data["text"] == "hello"
