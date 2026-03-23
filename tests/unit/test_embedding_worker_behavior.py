from __future__ import annotations

import pytest

from cina.ingestion.embedding.worker import _to_int, run_embedding_worker_once


class FakeQueue:
    def __init__(self, messages: list[dict[str, object] | None]) -> None:
        self.messages = messages
        self.acked: list[str] = []
        self.enqueued: list[tuple[dict[str, object], str]] = []
        self.dead_lettered: list[tuple[dict[str, object], str, str]] = []

    async def dequeue(self, _queue_name: str, wait_timeout_seconds: int):
        _ = wait_timeout_seconds
        if not self.messages:
            return None
        return self.messages.pop(0)

    async def acknowledge(self, receipt: str) -> None:
        self.acked.append(receipt)

    async def enqueue(self, message: dict[str, object], queue_name: str) -> str:
        self.enqueued.append((message, queue_name))
        return "id-1"

    async def dead_letter(self, message: dict[str, object], queue_name: str, reason: str) -> None:
        self.dead_lettered.append((message, queue_name, reason))


class FakeProvider:
    def __init__(self, vectors: list[list[float]] | None = None, fail: bool = False) -> None:
        self.vectors = vectors or [[0.1, 0.2]]
        self.fail = fail

    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        _ = (texts, model, dimensions)
        if self.fail:
            raise RuntimeError("embed failure")
        return self.vectors


@pytest.mark.asyncio
async def test_worker_returns_zero_when_idle() -> None:
    queue = FakeQueue([None, None])
    provider = FakeProvider()

    async def _update(*_args, **_kwargs) -> None:
        return None

    processed = await run_embedding_worker_once(
        queue,
        "q",
        provider,
        _update,
        batch_size=4,
        max_retries=2,
        idle_polls=2,
    )

    assert processed == 0


@pytest.mark.asyncio
async def test_worker_success_updates_embeddings_and_acks() -> None:
    queue = FakeQueue(
        [
            {
                "chunk_id": "c1",
                "content": "alpha",
                "content_hash": "h1",
                "embedding_model": "m1",
                "embedding_dim": 4,
                "retries": 0,
                "__receipt": "r1",
            },
            None,
            None,
        ]
    )
    provider = FakeProvider(vectors=[[0.3, 0.4]])
    updates: list[tuple[list[str], list[list[float]], str, int]] = []

    async def _update(
        chunk_ids: list[str],
        embeddings: list[list[float]],
        *,
        embedding_model: str,
        embedding_dim: int,
    ) -> None:
        updates.append((chunk_ids, embeddings, embedding_model, embedding_dim))

    processed = await run_embedding_worker_once(
        queue,
        "q",
        provider,
        _update,
        batch_size=2,
        max_retries=1,
        idle_polls=2,
    )

    assert processed == 1
    assert updates == [(["c1"], [[0.3, 0.4]], "m1", 4)]
    assert queue.acked == ["r1"]
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_worker_requeues_when_provider_fails_under_retry_limit() -> None:
    queue = FakeQueue(
        [
            {
                "chunk_id": "c2",
                "content": "beta",
                "content_hash": "h2",
                "embedding_model": "m2",
                "embedding_dim": 6,
                "retries": 0,
                "__receipt": "r2",
            }
        ]
    )
    provider = FakeProvider(fail=True)

    async def _update(*_args, **_kwargs) -> None:
        return None

    processed = await run_embedding_worker_once(
        queue,
        "q",
        provider,
        _update,
        batch_size=1,
        max_retries=2,
        idle_polls=1,
    )

    assert processed == 0
    assert len(queue.enqueued) == 1
    payload, queue_name = queue.enqueued[0]
    assert queue_name == "q"
    assert payload["retries"] == 1
    assert queue.acked == ["r2"]
    assert queue.dead_lettered == []


@pytest.mark.asyncio
async def test_worker_dead_letters_when_retry_limit_exceeded() -> None:
    queue = FakeQueue(
        [
            {
                "chunk_id": "c3",
                "content": "gamma",
                "content_hash": "h3",
                "embedding_model": "m3",
                "embedding_dim": 8,
                "retries": 2,
                "__receipt": "r3",
            }
        ]
    )
    provider = FakeProvider(fail=True)

    async def _update(*_args, **_kwargs) -> None:
        return None

    processed = await run_embedding_worker_once(
        queue,
        "q",
        provider,
        _update,
        batch_size=1,
        max_retries=2,
        idle_polls=1,
    )

    assert processed == 0
    assert queue.enqueued == []
    assert len(queue.dead_lettered) == 1
    dead_payload, dead_queue, reason = queue.dead_lettered[0]
    assert dead_queue == "q"
    assert dead_payload["chunk_id"] == "c3"
    assert "embed failure" in reason
    assert queue.acked == ["r3"]


def test_to_int_handles_supported_types_and_defaults() -> None:
    assert _to_int(None, default=9) == 9
    assert _to_int(True) == 1
    assert _to_int(7) == 7
    assert _to_int(7.9) == 7
    assert _to_int("12") == 12
    assert _to_int("bad", default=4) == 4
    assert _to_int(object(), default=3) == 3
