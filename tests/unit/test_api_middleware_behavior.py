from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from cina.api.middleware.correlation import CorrelationIDMiddleware
from cina.api.middleware.rate_limit import RateLimitMiddleware


@dataclass
class RateCheckResult:
    allowed: bool
    retry_after_seconds: int
    limit: int
    remaining: int


class FakeLimiter:
    def __init__(self, result: RateCheckResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def check(self, tenant_id: str) -> RateCheckResult:
        self.calls.append(tenant_id)
        return self.result


def _build_base_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/v1/query")
    async def query(request: Request) -> JSONResponse:
        tenant_id = getattr(request.state, "tenant_id", "anonymous")
        correlation_id = getattr(request.state, "correlation_id", "")
        return JSONResponse({"tenant_id": tenant_id, "correlation_id": correlation_id})

    return app


def test_correlation_middleware_propagates_or_generates_id() -> None:
    app = _build_base_app()
    app.add_middleware(CorrelationIDMiddleware)

    with TestClient(app) as client:
        provided = client.post("/v1/query", headers={"x-correlation-id": "cid-123"})
        generated = client.post("/v1/query")

    assert provided.headers["x-correlation-id"] == "cid-123"
    assert generated.headers["x-correlation-id"]


def test_rate_limit_middleware_denies_and_sets_headers() -> None:
    app = _build_base_app()
    app.state.rate_limiter = FakeLimiter(
        RateCheckResult(allowed=False, retry_after_seconds=7, limit=100, remaining=0)
    )

    @app.middleware("http")
    async def add_tenant(request: Request, call_next):
        request.state.tenant_id = "tenant-a"
        return await call_next(request)

    app.add_middleware(RateLimitMiddleware)

    with TestClient(app) as client:
        response = client.post("/v1/query")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.json()["detail"] == "rate_limit_exceeded"


def test_rate_limit_middleware_allows_and_adds_budget_headers() -> None:
    app = _build_base_app()
    limiter = FakeLimiter(
        RateCheckResult(allowed=True, retry_after_seconds=0, limit=100, remaining=55)
    )
    app.state.rate_limiter = limiter

    @app.middleware("http")
    async def add_tenant(request: Request, call_next):
        request.state.tenant_id = "tenant-b"
        return await call_next(request)

    app.add_middleware(RateLimitMiddleware)

    with TestClient(app) as client:
        response = client.post("/v1/query")
        bypass = client.get("/ping")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "100"
    assert response.headers["X-RateLimit-Remaining"] == "55"
    assert bypass.status_code == 200
    assert limiter.calls == ["anonymous"]
