from fastapi import APIRouter, Response

from cina.observability.metrics import render_metrics

router = APIRouter()


@router.get("/metrics")
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
