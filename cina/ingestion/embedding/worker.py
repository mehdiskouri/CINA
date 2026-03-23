"""Embedding worker loop utilities for queue-driven vector generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from cina.ingestion.embedding.protocol import EmbeddingProviderProtocol
    from cina.ingestion.queue.protocol import QueueProtocol


@dataclass(slots=True)
class EmbeddingTask:
    """One queue message mapped into embedding worker input."""

    chunk_id: str
    content: str
    content_hash: str
    embedding_model: str
    embedding_dim: int
    retries: int = 0
    receipt: str | None = None


class UpdateEmbeddingsFn(Protocol):
    """Callable contract for persistence layer embedding updates."""

    def __call__(
        self,
        chunk_ids: list[str],
        embeddings: list[list[float]],
        *,
        embedding_model: str,
        embedding_dim: int,
    ) -> Awaitable[None]:
        """Persist generated vectors for chunk ids."""
        ...


@dataclass(frozen=True, slots=True)
class EmbeddingWorkerConfig:
    """Runtime controls for one embedding worker pass."""

    batch_size: int
    max_retries: int
    idle_polls: int = 2


def _to_int(value: object, default: int = 0) -> int:
    """Best-effort conversion to integer with fallback."""
    result = default
    if value is None:
        return result
    if isinstance(value, bool):
        result = int(value)
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float):
        result = int(value)
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError:
            result = default
    else:
        try:
            result = int(str(value))
        except (TypeError, ValueError):
            result = default
    return result


async def _handle_failed_tasks(
    *,
    tasks: list[EmbeddingTask],
    queue: QueueProtocol,
    queue_name: str,
    max_retries: int,
    reason: str,
) -> None:
    """Requeue or dead-letter failed embedding tasks based on retry budget."""
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
                reason=reason,
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


async def run_embedding_worker_once(  # noqa: PLR0913
    queue: QueueProtocol,
    queue_name: str,
    provider: EmbeddingProviderProtocol,
    update_embeddings: UpdateEmbeddingsFn,
    *,
    config: EmbeddingWorkerConfig | None = None,
    batch_size: int | None = None,
    max_retries: int | None = None,
    idle_polls: int = 2,
) -> int:
    """Process one batch from queue and return number of embedded chunks."""
    if config is None:
        if batch_size is None or max_retries is None:
            error_message = "Either config or (batch_size and max_retries) must be provided"
            raise TypeError(error_message)
        config = EmbeddingWorkerConfig(
            batch_size=batch_size,
            max_retries=max_retries,
            idle_polls=idle_polls,
        )

    tasks: list[EmbeddingTask] = []
    idle_count = 0

    while len(tasks) < config.batch_size:
        message = await queue.dequeue(queue_name, wait_timeout_seconds=1)
        if message is None:
            idle_count += 1
            if idle_count >= config.idle_polls:
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
            ),
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
    except (RuntimeError, ValueError, TypeError, OSError, ConnectionError) as exc:
        await _handle_failed_tasks(
            tasks=tasks,
            queue=queue,
            queue_name=queue_name,
            max_retries=config.max_retries,
            reason=str(exc),
        )
        return 0
