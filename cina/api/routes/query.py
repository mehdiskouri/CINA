from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from cina.api.schemas.query import QueryRequest
from cina.observability.metrics import cina_query_total
from cina.serving.stream.sse import sse_event

router = APIRouter()


async def _stub_stream(query: str) -> AsyncIterator[str]:
    query_id = str(uuid4())
    yield sse_event("metadata", {"query_id": query_id, "cache_hit": False, "sources_used": 0})
    yield sse_event("token", {"text": "not implemented"})
    yield sse_event("citations", {"citations": []})
    yield sse_event("metrics", {"query": query})
    yield sse_event("done", {})


@router.post("/v1/query")
async def query_endpoint(request: QueryRequest) -> StreamingResponse:
    cina_query_total.inc()
    return StreamingResponse(_stub_stream(request.query), media_type="text/event-stream")
