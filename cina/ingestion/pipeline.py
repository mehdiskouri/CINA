"""End-to-end ingestion orchestration: fetch, parse, chunk, queue, and embed."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import UUID

from cina.config import load_config
from cina.db.connection import get_pool
from cina.db.repositories.chunk import ChunkRepository
from cina.db.repositories.document import DocumentRepository
from cina.ingestion.chunking.config import ChunkConfig
from cina.ingestion.chunking.engine import ChunkingEngine
from cina.ingestion.connectors import CONNECTOR_BY_SOURCE
from cina.ingestion.connectors.protocol import FetchConfig, RawDocument, SourceConnector
from cina.ingestion.embedding.openai import OpenAIEmbeddingProvider
from cina.ingestion.embedding.worker import EmbeddingWorkerConfig, run_embedding_worker_once
from cina.ingestion.queue import build_queue_backend

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import asyncpg

    from cina.ingestion.queue.protocol import QueueProtocol
    from cina.models.document import Document


@dataclass(slots=True)
class IngestionResult:
    """Final ingestion execution summary."""

    job_id: UUID
    documents_processed: int
    chunks_created: int
    chunks_embedded: int
    errors: list[str]


@dataclass(slots=True)
class IngestionProgress:
    """Progress callback payload emitted during ingestion."""

    phase: str
    documents_processed: int
    chunks_created: int
    chunks_embedded: int
    errors_count: int


@dataclass(frozen=True, slots=True)
class IngestionRunConfig:
    """Input configuration for one ingestion run."""

    source: str
    path: Path
    limit: int | None
    concurrency: int
    batch_size: int


@dataclass(frozen=True, slots=True)
class _IngestionDependencies:
    """Resolved runtime dependencies for ingestion processing."""

    connector: SourceConnector
    document_repo: DocumentRepository
    chunk_repo: ChunkRepository
    chunker: ChunkingEngine
    queue: QueueProtocol
    queue_name: str
    embedding_model: str
    embedding_dim: int


@dataclass(frozen=True, slots=True)
class _IngestionCounters:
    """Mutable counters collapsed into an immutable transfer object."""

    documents_processed: int
    chunks_created: int
    chunks_embedded: int
    errors: list[str]


def _emit_progress(
    *,
    progress_callback: Callable[[IngestionProgress], None] | None,
    phase: str,
    counters: _IngestionCounters,
) -> None:
    """Emit progress payload when callback is configured."""
    if progress_callback is None:
        return
    progress_callback(
        IngestionProgress(
            phase=phase,
            documents_processed=counters.documents_processed,
            chunks_created=counters.chunks_created,
            chunks_embedded=counters.chunks_embedded,
            errors_count=len(counters.errors),
        ),
    )


async def run_ingestion(
    *,
    config: IngestionRunConfig,
    progress_callback: Callable[[IngestionProgress], None] | None = None,
) -> IngestionResult:
    """Execute one ingestion run from document fetch to embedding completion."""
    cfg = load_config()
    source = config.source
    if source not in CONNECTOR_BY_SOURCE:
        message = f"Unsupported source: {source}"
        raise ValueError(message)

    pool = await get_pool()
    deps = _IngestionDependencies(
        connector=cast("SourceConnector", CONNECTOR_BY_SOURCE[source]()),
        document_repo=DocumentRepository(pool),
        chunk_repo=ChunkRepository(pool),
        chunker=ChunkingEngine(
            ChunkConfig(
                max_chunk_tokens=cfg.ingestion.chunk.max_tokens,
                overlap_tokens=cfg.ingestion.chunk.overlap_tokens,
                tokenizer=cfg.ingestion.chunk.tokenizer,
                respect_section_boundaries=cfg.ingestion.chunk.respect_sections,
                sentence_boundary_alignment=cfg.ingestion.chunk.sentence_alignment,
            ),
        ),
        queue=build_queue_backend(),
        queue_name=cfg.ingestion.queue.name,
        embedding_model=cfg.ingestion.embedding.model,
        embedding_dim=cfg.ingestion.embedding.dimensions,
    )
    provider = OpenAIEmbeddingProvider(api_key=os.getenv("OPENAI_API_KEY"))

    job_id = await _create_ingestion_job(pool, source)

    counters = _IngestionCounters(
        documents_processed=0,
        chunks_created=0,
        chunks_embedded=0,
        errors=[],
    )
    semaphore = asyncio.Semaphore(max(1, config.concurrency))
    pending: list[asyncio.Task[tuple[int, str | None]]] = []

    async for raw in deps.connector.fetch_document_list(
        FetchConfig(limit=config.limit, source_path=config.path, glob_pattern="*"),
    ):

        async def _process(raw_document: RawDocument = raw) -> tuple[int, str | None]:
            async with semaphore:
                return await _process_single_document(
                    raw_document,
                    deps=deps,
                    ingestion_id=job_id,
                )

        pending.append(asyncio.create_task(_process()))

    for task in asyncio.as_completed(pending):
        created, error = await task
        if error:
            counters.errors.append(error)
            continue
        counters = _IngestionCounters(
            documents_processed=counters.documents_processed + 1,
            chunks_created=counters.chunks_created + created,
            chunks_embedded=counters.chunks_embedded,
            errors=counters.errors,
        )
        await _update_job_progress(
            pool,
            job_id,
            counters.documents_processed,
            counters.chunks_created,
        )
        _emit_progress(progress_callback=progress_callback, phase="documents", counters=counters)

    while True:
        processed = await run_embedding_worker_once(
            deps.queue,
            deps.queue_name,
            provider,
            deps.chunk_repo.update_embeddings,
            config=EmbeddingWorkerConfig(
                batch_size=config.batch_size,
                max_retries=cfg.ingestion.embedding.max_retries,
                idle_polls=2,
            ),
        )
        if processed == 0:
            break
        counters = _IngestionCounters(
            documents_processed=counters.documents_processed,
            chunks_created=counters.chunks_created,
            chunks_embedded=counters.chunks_embedded + processed,
            errors=counters.errors,
        )
        _emit_progress(progress_callback=progress_callback, phase="embeddings", counters=counters)

    await _finalize_ingestion_job(
        pool,
        job_id,
        documents_processed=counters.documents_processed,
        chunks_created=counters.chunks_created,
        errors=counters.errors,
    )

    _emit_progress(progress_callback=progress_callback, phase="finalized", counters=counters)

    return IngestionResult(
        job_id=job_id,
        documents_processed=counters.documents_processed,
        chunks_created=counters.chunks_created,
        chunks_embedded=counters.chunks_embedded,
        errors=counters.errors,
    )


async def _process_single_document(  # noqa: PLR0913
    raw_document: RawDocument,
    *,
    deps: _IngestionDependencies | None = None,
    connector: SourceConnector | None = None,
    document_repo: DocumentRepository | None = None,
    chunk_repo: ChunkRepository | None = None,
    chunker: ChunkingEngine | None = None,
    queue: QueueProtocol | None = None,
    queue_name: str | None = None,
    embedding_model: str | None = None,
    embedding_dim: int | None = None,
    ingestion_id: UUID,
) -> tuple[int, str | None]:
    """Parse, persist, and enqueue embeddings for one raw document."""
    resolved_deps = deps
    if resolved_deps is None:
        if (
            connector is None
            or document_repo is None
            or chunk_repo is None
            or chunker is None
            or queue is None
            or queue_name is None
            or embedding_model is None
            or embedding_dim is None
        ):
            message = "Missing legacy ingestion dependencies for _process_single_document"
            raise TypeError(message)
        resolved_deps = _IngestionDependencies(
            connector=connector,
            document_repo=document_repo,
            chunk_repo=chunk_repo,
            chunker=chunker,
            queue=queue,
            queue_name=queue_name,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
        )

    try:
        document: Document = resolved_deps.connector.parse(raw_document)
        document_id = await resolved_deps.document_repo.upsert_document(
            document,
            ingestion_id=ingestion_id,
        )
        document.id = document_id
        for section in document.sections:
            section.document_id = document_id
        await resolved_deps.document_repo.replace_sections(document_id, document.sections)

        chunks = resolved_deps.chunker.chunk_document(
            document,
            embedding_model=resolved_deps.embedding_model,
        )
        inserted = await resolved_deps.chunk_repo.bulk_upsert(chunks)

        pending_embeddings = await resolved_deps.chunk_repo.get_unembedded_by_hashes(
            embedding_model=resolved_deps.embedding_model,
            content_hashes=[chunk.content_hash for chunk in chunks],
        )
        for pending in pending_embeddings:
            await resolved_deps.queue.enqueue(
                {
                    "chunk_id": pending["id"],
                    "content": pending["content"],
                    "content_hash": pending["content_hash"],
                    "embedding_model": resolved_deps.embedding_model,
                    "embedding_dim": resolved_deps.embedding_dim,
                    "retries": 0,
                },
                resolved_deps.queue_name,
            )
    except (RuntimeError, ValueError, TypeError, OSError, ConnectionError) as exc:
        return 0, f"{raw_document.source_id}: {exc}"
    else:
        return inserted, None


async def _create_ingestion_job(pool: asyncpg.Pool, source: str) -> UUID:
    """Create and return an ingestion job id in running state."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ingestion_jobs (source, status, started_at)
            VALUES ($1::source_type, 'running', now())
            RETURNING id
            """,
            source,
        )
        if row is None:
            message = "Failed to create ingestion job"
            raise RuntimeError(message)
        return UUID(str(row["id"]))


async def _update_job_progress(
    pool: asyncpg.Pool,
    job_id: UUID,
    documents_done: int,
    chunks_created: int,
) -> None:
    """Update ingestion job counters during document processing."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ingestion_jobs
            SET documents_done = $2,
                chunks_created = $3
            WHERE id = $1
            """,
            job_id,
            documents_done,
            chunks_created,
        )


async def _finalize_ingestion_job(
    pool: asyncpg.Pool,
    job_id: UUID,
    *,
    documents_processed: int,
    chunks_created: int,
    errors: list[str],
) -> None:
    """Mark ingestion job as completed/failed and persist final counters."""
    status = "failed" if errors else "completed"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = $2::ingestion_status,
                documents_total = $3,
                documents_done = $3,
                chunks_created = $4,
                errors = $5::jsonb,
                completed_at = now()
            WHERE id = $1
            """,
            job_id,
            status,
            documents_processed,
            chunks_created,
            json.dumps(errors),
        )


async def run_embedding_worker_service(
    *,
    batch_size: int = 64,
    poll_interval_seconds: float = 1.0,
) -> None:
    """Run long-lived embedding worker polling loop."""
    cfg = load_config()
    queue = build_queue_backend()
    queue_name = cfg.ingestion.queue.name
    provider = OpenAIEmbeddingProvider(api_key=os.getenv("OPENAI_API_KEY"))
    pool = await get_pool()
    chunk_repo = ChunkRepository(pool)

    while True:
        await run_embedding_worker_once(
            queue,
            queue_name,
            provider,
            chunk_repo.update_embeddings,
            config=EmbeddingWorkerConfig(
                batch_size=batch_size,
                max_retries=cfg.ingestion.embedding.max_retries,
                idle_polls=2,
            ),
        )
        await asyncio.sleep(max(0.1, poll_interval_seconds))
