from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from cina.ingestion.embedding.protocol import EmbeddingProviderProtocol
from cina.ingestion.queue.protocol import QueueProtocol


@dataclass(slots=True)
class EmbeddingTask:
	chunk_id: str
	content: str
	content_hash: str
	embedding_model: str
	embedding_dim: int
	retries: int = 0
	receipt: str | None = None


class UpdateEmbeddingsFn(Protocol):
	def __call__(
		self,
		chunk_ids: list[str],
		embeddings: list[list[float]],
		*,
		embedding_model: str,
		embedding_dim: int,
	) -> Awaitable[None]: ...


def _to_int(value: object, default: int = 0) -> int:
	if value is None:
		return default
	if isinstance(value, bool):
		return int(value)
	if isinstance(value, int):
		return value
	if isinstance(value, float):
		return int(value)
	if isinstance(value, str):
		try:
			return int(value)
		except ValueError:
			return default
	return default


async def run_embedding_worker_once(
    queue: QueueProtocol,
    queue_name: str,
    provider: EmbeddingProviderProtocol,
    update_embeddings: UpdateEmbeddingsFn,
    *,
    batch_size: int,
    max_retries: int,
    idle_polls: int = 2,
) -> int:
    tasks: list[EmbeddingTask] = []
    idle_count = 0

    while len(tasks) < batch_size:
        message = await queue.dequeue(queue_name, wait_timeout_seconds=1)
        if message is None:
            idle_count += 1
            if idle_count >= idle_polls:
                break
            continue

        idle_count = 0
        tasks.append(
            EmbeddingTask(
                chunk_id=str(message["chunk_id"]),
                content=str(message["content"]),
                content_hash=str(message["content_hash"]),
                embedding_model=str(message["embedding_model"]),
                embedding_dim=_to_int(message.get("embedding_dim"), default=0),
                retries=_to_int(message.get("retries"), default=0),
                receipt=str(message["__receipt"]),
            )
        )

    if not tasks:
        return 0

    texts = [task.content for task in tasks]
    model = tasks[0].embedding_model
    dimensions = tasks[0].embedding_dim

    try:
        vectors = await provider.embed(texts, model=model, dimensions=dimensions)
        await update_embeddings(
            [task.chunk_id for task in tasks],
            vectors,
            embedding_model=model,
            embedding_dim=dimensions,
        )
        for task in tasks:
            if task.receipt:
                await queue.acknowledge(task.receipt)
        return len(tasks)
    except Exception as exc:
        for task in tasks:
            next_retries = task.retries + 1
            if next_retries > max_retries:
                await queue.dead_letter(
                    {
                        "chunk_id": task.chunk_id,
                        "content_hash": task.content_hash,
                        "embedding_model": task.embedding_model,
                    },
                    queue_name,
                    reason=str(exc),
                )
                if task.receipt:
                    await queue.acknowledge(task.receipt)
                continue

            await queue.enqueue(
                {
                    "chunk_id": task.chunk_id,
                    "content": task.content,
                    "content_hash": task.content_hash,
                    "embedding_model": task.embedding_model,
                    "embedding_dim": task.embedding_dim,
                    "retries": next_retries,
                },
                queue_name,
            )
            if task.receipt:
                await queue.acknowledge(task.receipt)
        return 0
