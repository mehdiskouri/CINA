from __future__ import annotations

import runpy


def test_module_entrypoint_invokes_cli_app(monkeypatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr("cina.cli.main.app", lambda: called.append(True))

    runpy.run_module("cina.__main__", run_name="__main__")

    assert called == [True]
