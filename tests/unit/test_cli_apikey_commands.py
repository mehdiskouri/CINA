from __future__ import annotations

from uuid import uuid4

from typer.testing import CliRunner

import cina.cli.apikey as cli_apikey


class FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_apikey_create_command_outputs_id_and_key(monkeypatch) -> None:
    runner = CliRunner()
    key_id = uuid4()

    async def _create_pool() -> FakePool:
        return FakePool()

    class FakeRepo:
        def __init__(self, _pool) -> None:
            pass

        async def create_key(self, *, key_hash: str, tenant_id: str, name: str):
            _ = (key_hash, tenant_id, name)
            return key_id

    monkeypatch.setattr(cli_apikey, "create_pool", _create_pool)
    monkeypatch.setattr(cli_apikey, "APIKeyRepository", FakeRepo)
    monkeypatch.setattr(cli_apikey, "_new_key", lambda: "cina_sk_fixed")
    monkeypatch.setattr(cli_apikey.bcrypt, "gensalt", lambda: b"salt")
    monkeypatch.setattr(cli_apikey.bcrypt, "hashpw", lambda *_args: b"hash")

    result = runner.invoke(cli_apikey.app, ["create", "--tenant", "tenant-a", "--name", "main"])

    assert result.exit_code == 0
    assert f"id={key_id}" in result.stdout
    assert "api_key=cina_sk_fixed" in result.stdout


def test_apikey_revoke_and_list_commands(monkeypatch) -> None:
    runner = CliRunner()

    async def _create_pool() -> FakePool:
        return FakePool()

    class FakeRepo:
        def __init__(self, _pool) -> None:
            pass

        async def revoke_key(self, _id: str) -> bool:
            return True

        async def list_keys(self, _tenant: str | None):
            return [
                {
                    "id": "id-1",
                    "tenant_id": "tenant-a",
                    "name": "main",
                    "active": True,
                }
            ]

    monkeypatch.setattr(cli_apikey, "create_pool", _create_pool)
    monkeypatch.setattr(cli_apikey, "APIKeyRepository", FakeRepo)

    revoke = runner.invoke(cli_apikey.app, ["revoke", "--id", "id-1"])
    listed = runner.invoke(cli_apikey.app, ["list", "--tenant", "tenant-a"])

    assert revoke.exit_code == 0
    assert "revoked" in revoke.stdout
    assert listed.exit_code == 0
    assert "tenant=tenant-a" in listed.stdout
