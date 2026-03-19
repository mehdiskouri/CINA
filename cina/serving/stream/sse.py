import json


def sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\\ndata: {json.dumps(data)}\\n\\n"
