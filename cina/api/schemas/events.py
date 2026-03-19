from pydantic import BaseModel


class SSEEvent(BaseModel):
    event: str
    data: dict[str, object]
