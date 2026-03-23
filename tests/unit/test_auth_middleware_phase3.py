from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from cina.api.middleware.auth import APIKeyAuthMiddleware


async def _query_endpoint(request):
    return JSONResponse({"tenant": getattr(request.state, "tenant_id", None)})


def _build_app() -> Starlette:
    app = Starlette(routes=[Route("/v1/query", _query_endpoint, methods=["POST"])])
    app.add_middleware(APIKeyAuthMiddleware)
    return app


@pytest.mark.asyncio
async def test_auth_missing_token_returns_401() -> None:
    app = _build_app()
    app.state.apikey_repo = SimpleNamespace(validate_token=AsyncMock(return_value=None))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/query", json={"query": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_invalid_token_returns_401() -> None:
    app = _build_app()
    app.state.apikey_repo = SimpleNamespace(validate_token=AsyncMock(return_value=None))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/query",
            json={"query": "x"},
            headers={"Authorization": "Bearer invalid"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_valid_token_sets_tenant() -> None:
    app = _build_app()
    app.state.apikey_repo = SimpleNamespace(
        validate_token=AsyncMock(return_value=SimpleNamespace(tenant_id="demo", name="k1")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/query",
            json={"query": "x"},
            headers={"Authorization": "Bearer good"},
        )
    assert resp.status_code == 200
    assert resp.json()["tenant"] == "demo"
