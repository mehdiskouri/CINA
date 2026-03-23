from __future__ import annotations

from dataclasses import dataclass

import pytest

from cina.ingestion.embedding.worker import run_embedding_worker_once


@dataclass
class InMemoryQueue:
    queued: list[dict[str, object]]
    acknowledged: list[str]
    dead_letters: list[dict[str, object]]

    async def enqueue(self, message: dict[str, object], queue_name: str) -> str:
        self.queued.append({**message, "_queue": queue_name})
        return str(len(self.queued))

    async def dequeue(self, queue_name: str, wait_timeout_seconds: int) -> dict[str, object] | None:
        for idx, message in enumerate(self.queued):
            if message.get("_queue") == queue_name and "__receipt" in message:
                return self.queued.pop(idx)
        return None

    async def acknowledge(self, receipt: str) -> None:
        self.acknowledged.append(receipt)

    async def dead_letter(self, message: dict[str, object], queue_name: str, reason: str) -> None:
        self.dead_letters.append({**message, "queue": queue_name, "reason": reason})


class StubProvider:
    async def embed(self, texts: list[str], model: str, dimensions: int) -> list[list[float]]:
        return [[0.1] * dimensions for _ in texts]

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_embedding_worker_processes_and_acknowledges_tasks() -> None:
    queue = InMemoryQueue(
        queued=[
            {
                "_queue": "cina:queue:ingestion",
                "chunk_id": "abc",
                "content": "sample text",
                "content_hash": "hash-1",
                "embedding_model": "text-embedding-3-small",
                "embedding_dim": 4,
                "retries": 0,
                "__receipt": "stream|1-0",
            },
        ],
        acknowledged=[],
        dead_letters=[],
    )
    calls: list[tuple[list[str], list[list[float]], str, int]] = []

    async def update_embeddings(
        chunk_ids: list[str],
        embeddings: list[list[float]],
        *,
        embedding_model: str,
        embedding_dim: int,
    ) -> None:
        calls.append((chunk_ids, embeddings, embedding_model, embedding_dim))

    processed = await run_embedding_worker_once(
        queue,
        "cina:queue:ingestion",
        StubProvider(),
        update_embeddings,
        batch_size=8,
        max_retries=2,
        idle_polls=1,
    )

    assert processed == 1
    assert len(calls) == 1
    assert calls[0][0] == ["abc"]
    assert calls[0][2] == "text-embedding-3-small"
    assert calls[0][3] == 4
    assert queue.acknowledged == ["stream|1-0"]
    assert queue.dead_letters == []
