from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace

import pytest

import cina.cli.db as cli_db


class _TransactionContext(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


class FakeConnection:
    def __init__(self, applied: set[str]) -> None:
        self.applied = applied
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    async def fetch(self, _query: str) -> list[dict[str, str]]:
        return [{"version": v} for v in sorted(self.applied)]

    async def execute(self, query: str, *args: object) -> None:
        self.executed.append((query, args))
        if query.startswith("INSERT INTO schema_migrations") and args:
            self.applied.add(str(args[0]))

    def transaction(self) -> _TransactionContext:
        return _TransactionContext()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_run_migrations_returns_zero_when_no_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_conn = FakeConnection(set())
    output: list[str] = []

    monkeypatch.setattr(
        cli_db,
        "load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(postgres=SimpleNamespace(dsn_env="DATABASE_URL")),
        ),
    )
    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setattr(cli_db, "_migrations_dir", lambda: tmp_path)

    async def _connect(_dsn: str) -> FakeConnection:
        return fake_conn

    monkeypatch.setattr(cli_db.asyncpg, "connect", _connect)
    monkeypatch.setattr(cli_db.typer, "echo", lambda message: output.append(str(message)))

    out = await cli_db.run_migrations()

    assert out == 0
    assert output == ["No migrations found"]


@pytest.mark.asyncio
async def test_run_migrations_applies_only_pending_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "002_add_table.sql").write_text("SELECT 2;", encoding="utf-8")

    fake_conn = FakeConnection({"001_init.sql"})
    output: list[str] = []

    monkeypatch.setattr(
        cli_db,
        "load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(postgres=SimpleNamespace(dsn_env="DATABASE_URL")),
        ),
    )
    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setattr(cli_db, "_migrations_dir", lambda: tmp_path)

    async def _connect(_dsn: str) -> FakeConnection:
        return fake_conn

    monkeypatch.setattr(cli_db.asyncpg, "connect", _connect)
    monkeypatch.setattr(cli_db.typer, "echo", lambda message: output.append(str(message)))

    out = await cli_db.run_migrations()

    assert out == 1
    assert "Applied migration: 002_add_table.sql" in output
    assert fake_conn.closed is True
