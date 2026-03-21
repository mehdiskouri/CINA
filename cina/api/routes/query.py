from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from cina.api.schemas.query import QueryRequest
from cina.observability.metrics import cina_query_total

router = APIRouter()


@router.post("/v1/query")
async def query_endpoint(request: QueryRequest, req: Request) -> StreamingResponse:
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
