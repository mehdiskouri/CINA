from __future__ import annotations

from types import SimpleNamespace

import cina.cli.serve as cli_serve


def test_serve_root_invokes_uvicorn_run(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        __import__("sys").modules,
        "uvicorn",
        SimpleNamespace(run=lambda *a, **k: calls.append({"args": a, "kwargs": k})),
    )

    cli_serve.serve_root(host="127.0.0.1", port=9001, reload=True, workers=2)

    assert len(calls) == 1
    assert calls[0]["args"] == ("cina.api.app:app",)
    assert calls[0]["kwargs"]["host"] == "127.0.0.1"
    assert calls[0]["kwargs"]["port"] == 9001
