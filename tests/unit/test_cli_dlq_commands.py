from __future__ import annotations

import json

from typer.testing import CliRunner

import cina.cli.dlq as cli_dlq


class FakeRedis:
    def __init__(self) -> None:
        self.added: list[tuple[str, dict[str, str]]] = []
        self.deleted_ids: list[tuple[str, str]] = []
        self.deleted_keys: list[str] = []

    async def xrevrange(self, _stream: str, count: int):
        _ = count
        return [(b"1-0", {b"payload": b'{"chunk_id":"c1"}'})]

    async def xrange(self, _stream: str, min: str, max: str, count: int):
        _ = (max, count)
        if min == "missing":
            return []
        return [("1-0", {"payload": '{"chunk_id":"c2"}'})]

    async def xadd(self, stream: str, payload: dict[str, str]) -> None:
        self.added.append((stream, payload))

    async def xdel(self, stream: str, msg_id: str) -> None:
        self.deleted_ids.append((stream, msg_id))

    async def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        return 1

    async def aclose(self) -> None:
        return None


def test_dlq_list_retry_and_purge_commands(monkeypatch) -> None:
    runner = CliRunner()
    fake_redis = FakeRedis()

    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda _url: fake_redis)
    monkeypatch.setattr(cli_dlq, "_redis_url", lambda: "redis://localhost:6379/0")

    listed = runner.invoke(cli_dlq.app, ["list", "--queue", "ingestion", "--limit", "5"])
    retried = runner.invoke(cli_dlq.app, ["retry", "--id", "1-0", "--queue", "ingestion"])
    purged = runner.invoke(cli_dlq.app, ["purge", "--queue", "ingestion"])

    assert listed.exit_code == 0
    assert "payload={'chunk_id': 'c1'}" in listed.stdout
    assert retried.exit_code == 0
    assert "retried" in retried.stdout
    assert fake_redis.added
    assert json.loads(fake_redis.added[0][1]["payload"]) == {"chunk_id": "c2"}
    assert purged.exit_code == 0
    assert "deleted=1" in purged.stdout


def test_dlq_retry_not_found(monkeypatch) -> None:
    runner = CliRunner()
    fake_redis = FakeRedis()

    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda _url: fake_redis)
    monkeypatch.setattr(cli_dlq, "_redis_url", lambda: "redis://localhost:6379/0")

    result = runner.invoke(cli_dlq.app, ["retry", "--id", "missing", "--queue", "ingestion"])

    assert result.exit_code == 0
    assert "not_found" in result.stdout
