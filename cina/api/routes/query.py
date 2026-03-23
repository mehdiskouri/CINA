"""Query endpoint that streams serving pipeline SSE responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from cina.observability.metrics import cina_query_total

if TYPE_CHECKING:
    from cina.api.schemas.query import QueryRequest

router = APIRouter()


@router.post("/v1/query")
async def query_endpoint(request: QueryRequest, req: Request) -> StreamingResponse:
    """Handle query requests and stream token/citation events via SSE."""
    cina_query_total.inc()
    pipeline = req.app.state.serving_pipeline
    tenant_id = getattr(req.state, "tenant_id", None)
    return StreamingResponse(
        pipeline.stream_query(request.query, tenant_id=tenant_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
