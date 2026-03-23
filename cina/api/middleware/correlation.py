"""Request correlation-id middleware for structured logging and tracing."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from cina.observability.logging import correlation_id_var, get_logger

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

logger = get_logger("cina.api")


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """Populate request correlation IDs and emit start/finish request logs."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Set correlation ID in contextvar and response headers for each request."""
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
