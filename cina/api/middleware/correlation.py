from __future__ import annotations

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from cina.observability.logging import correlation_id_var, get_logger

logger = get_logger("cina.api")


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = request.headers.get("x-correlation-id", str(uuid4()))
        token = correlation_id_var.set(correlation_id)
        request.state.correlation_id = correlation_id
        try:
            logger.info(
                "request_started",
                method=request.method,
                path=request.url.path,
            )
            response = await call_next(request)
            response.headers["x-correlation-id"] = correlation_id
            logger.info(
                "request_finished",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            )
            return response
        finally:
            correlation_id_var.reset(token)
