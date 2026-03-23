"""Bearer API key authentication middleware."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, ClassVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Validate bearer API keys and attach tenant metadata to request state."""

    EXEMPT_PATHS: ClassVar[set[str]] = {"/health", "/ready", "/metrics"}

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Authenticate incoming requests unless endpoint is explicitly exempt."""
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        repo = getattr(request.app.state, "apikey_repo", None)
        if repo is None:
            return JSONResponse(status_code=500, content={"detail": "auth_not_initialized"})

        if os.getenv("CINA_AUTH_DISABLED", "0") == "1" and request.url.path == "/v1/query":
            request.state.tenant_id = "dev"
            request.state.apikey_name = "disabled"
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "missing_bearer_token"})

        token = auth[len("Bearer ") :].strip()
        record = await repo.validate_token(token)
        if record is None:
            return JSONResponse(status_code=401, content={"detail": "invalid_api_key"})

        request.state.tenant_id = record.tenant_id
        request.state.apikey_name = record.name
        return await call_next(request)
