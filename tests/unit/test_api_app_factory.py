from __future__ import annotations

from types import SimpleNamespace

from cina.api import app as app_module


def test_create_app_wires_routes_and_middlewares(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        app_module,
        "load_config",
        lambda: SimpleNamespace(observability=SimpleNamespace(log_level="DEBUG")),
    )
    monkeypatch.setattr(
        app_module,
        "configure_logging",
        lambda level: calls.append(level),
    )

    app = app_module.create_app()

    route_paths = {route.path for route in app.routes}

    assert calls == ["DEBUG"]
    assert "/health" in route_paths
    assert "/metrics" in route_paths
    assert "/v1/query" in route_paths
    assert len(app.user_middleware) >= 3
