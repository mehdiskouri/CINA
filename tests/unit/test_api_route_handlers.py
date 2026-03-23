from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cina.api.routes.health import router as health_router
from cina.api.routes.metrics import router as metrics_router
from cina.api.routes.query import router as query_router


class FakePipeline:
    async def stream_query(self, query: str, *, tenant_id: str | None = None):
        _ = (query, tenant_id)
        yield 'event: token\ndata: {"text":"ok"}\n\n'


def test_health_and_ready_routes(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(health_router)

    async def _ok() -> dict[str, str]:
        return {"status": "ok"}

    monkeypatch.setattr("cina.api.routes.health.db_healthcheck", _ok)
    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    assert ready.json() == {"status": "ready"}


def test_metrics_route_returns_payload(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(metrics_router)
    monkeypatch.setattr("cina.api.routes.metrics.render_metrics", lambda: ("m 1\n", "text/plain"))

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.text == "m 1\n"
    assert response.headers["content-type"].startswith("text/plain")


def test_query_route_streams_sse() -> None:
    app = FastAPI()
    app.include_router(query_router)
    app.state.serving_pipeline = FakePipeline()

    async def _tenant_middleware(request, call_next):
        request.state.tenant_id = "tenant-1"
        return await call_next(request)

    app.middleware("http")(_tenant_middleware)

    with TestClient(app) as client:
        response = client.post("/v1/query", json={"query": "her2"})

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: token" in response.text
