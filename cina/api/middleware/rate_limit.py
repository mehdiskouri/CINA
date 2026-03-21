"""HTTP rate limit middleware for tenant-scoped requests."""

from __future__ import annotations

import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from cina.observability.metrics import cina_rate_limit_exceeded_total


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        limiter = getattr(request.app.state, "rate_limiter", None)
        if limiter is None or request.url.path != "/v1/query":
            return await call_next(request)

        tenant_id = getattr(request.state, "tenant_id", "anonymous")
        result = await limiter.check(tenant_id)
        if not result.allowed:
            cina_rate_limit_exceeded_total.labels(tenant=tenant_id).inc()
            return Response(
                content=json.dumps({"detail": "rate_limit_exceeded"}),
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(result.retry_after_seconds),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": str(result.remaining),
                    "X-RateLimit-Reset": str(result.retry_after_seconds),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Reset"] = "60"
        return response
