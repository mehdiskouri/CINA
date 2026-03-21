from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

import asyncpg

from cina.config import load_config
from cina.db.connection import get_pool
from cina.db.repositories.chunk import ChunkRepository
from cina.db.repositories.document import DocumentRepository
from cina.ingestion.chunking.config import ChunkConfig
from cina.ingestion.chunking.engine import ChunkingEngine
from cina.ingestion.connectors import CONNECTOR_BY_SOURCE
from cina.ingestion.connectors.protocol import FetchConfig, RawDocument, SourceConnector
from cina.ingestion.embedding.openai import OpenAIEmbeddingProvider
from cina.ingestion.embedding.worker import run_embedding_worker_once
from cina.ingestion.queue import build_queue_backend
from cina.ingestion.queue.protocol import QueueProtocol
from cina.models.document import Document


@dataclass(slots=True)
class IngestionResult:
    job_id: UUID
    documents_processed: int
    chunks_created: int
    chunks_embedded: int
    errors: list[str]


@dataclass(slots=True)
class IngestionProgress:
    phase: str
    documents_processed: int
    chunks_created: int
    chunks_embedded: int
    errors_count: int


async def run_ingestion(
    *,
    source: str,
    path: Path,
    limit: int | None,
    concurrency: int,
    batch_size: int,
    progress_callback: Callable[[IngestionProgress], None] | None = None,
) -> IngestionResult:
    cfg = load_config()
    if source not in CONNECTOR_BY_SOURCE:
        raise ValueError(f"Unsupported source: {source}")

    pool = await get_pool()
    document_repo = DocumentRepository(pool)
    chunk_repo = ChunkRepository(pool)
    connector = cast(SourceConnector, CONNECTOR_BY_SOURCE[source]())
    chunker = ChunkingEngine(
        ChunkConfig(
            max_chunk_tokens=cfg.ingestion.chunk.max_tokens,
            overlap_tokens=cfg.ingestion.chunk.overlap_tokens,
            tokenizer=cfg.ingestion.chunk.tokenizer,
            respect_section_boundaries=cfg.ingestion.chunk.respect_sections,
            sentence_boundary_alignment=cfg.ingestion.chunk.sentence_alignment,
        )
    )
    queue = build_queue_backend()
    provider = OpenAIEmbeddingProvider(api_key=os.getenv("OPENAI_API_KEY"))
    queue_name = cfg.ingestion.queue.name

    job_id = await _create_ingestion_job(pool, source)

    documents_processed = 0
    chunks_created = 0
    chunks_embedded = 0
    errors: list[str] = []
    semaphore = asyncio.Semaphore(max(1, concurrency))
    pending: list[asyncio.Task[tuple[int, str | None]]] = []

    async for raw in connector.fetch_document_list(
        FetchConfig(limit=limit, source_path=path, glob_pattern="*")
    ):

        async def _process(raw_document: RawDocument = raw) -> tuple[int, str | None]:
            async with semaphore:
                return await _process_single_document(
                    raw_document,
                    connector=connector,
                    document_repo=document_repo,
                    chunk_repo=chunk_repo,
                    chunker=chunker,
                    queue=queue,
                    queue_name=queue_name,
                    ingestion_id=job_id,
                    embedding_model=cfg.ingestion.embedding.model,
                    embedding_dim=cfg.ingestion.embedding.dimensions,
                )

        pending.append(asyncio.create_task(_process()))

    for task in asyncio.as_completed(pending):
        created, error = await task
        if error:
            errors.append(error)
            continue
        documents_processed += 1
        chunks_created += created
        await _update_job_progress(pool, job_id, documents_processed, chunks_created)
        if progress_callback is not None:
            progress_callback(
                IngestionProgress(
                    phase="documents",
                    documents_processed=documents_processed,
                    chunks_created=chunks_created,
                    chunks_embedded=chunks_embedded,
                    errors_count=len(errors),
                )
            )

    while True:
        processed = await run_embedding_worker_once(
            queue,
            queue_name,
            provider,
            chunk_repo.update_embeddings,
            batch_size=batch_size,
            max_retries=cfg.ingestion.embedding.max_retries,
            idle_polls=2,
        )
        if processed == 0:
            break
        chunks_embedded += processed
        if progress_callback is not None:
            progress_callback(
                IngestionProgress(
                    phase="embeddings",
                    documents_processed=documents_processed,
                    chunks_created=chunks_created,
                    chunks_embedded=chunks_embedded,
                    errors_count=len(errors),
                )
            )

    await _finalize_ingestion_job(
        pool,
        job_id,
        documents_processed=documents_processed,
        chunks_created=chunks_created,
        errors=errors,
    )

    if progress_callback is not None:
        progress_callback(
            IngestionProgress(
                phase="finalized",
                documents_processed=documents_processed,
                chunks_created=chunks_created,
                chunks_embedded=chunks_embedded,
                errors_count=len(errors),
            )
        )

    return IngestionResult(
        job_id=job_id,
        documents_processed=documents_processed,
        chunks_created=chunks_created,
        chunks_embedded=chunks_embedded,
        errors=errors,
    )


async def _process_single_document(
    raw_document: RawDocument,
    *,
    connector: SourceConnector,
    document_repo: DocumentRepository,
    chunk_repo: ChunkRepository,
    chunker: ChunkingEngine,
    queue: QueueProtocol,
    queue_name: str,
    ingestion_id: UUID,
    embedding_model: str,
    embedding_dim: int,
) -> tuple[int, str | None]:
    try:
        document: Document = connector.parse(raw_document)
        document_id = await document_repo.upsert_document(document, ingestion_id=ingestion_id)
        document.id = document_id
        for section in document.sections:
            section.document_id = document_id
        await document_repo.replace_sections(document_id, document.sections)

        chunks = chunker.chunk_document(document, embedding_model=embedding_model)
        inserted = await chunk_repo.bulk_upsert(chunks)

        pending_embeddings = await chunk_repo.get_unembedded_by_hashes(
            embedding_model=embedding_model,
            content_hashes=[chunk.content_hash for chunk in chunks],
        )
        for pending in pending_embeddings:
            await queue.enqueue(
                {
                    "chunk_id": pending["id"],
                    "content": pending["content"],
                    "content_hash": pending["content_hash"],
                    "embedding_model": embedding_model,
                    "embedding_dim": embedding_dim,
                    "retries": 0,
                },
                queue_name,
            )
        return inserted, None
    except Exception as exc:
        return 0, f"{raw_document.source_id}: {exc}"


async def _create_ingestion_job(pool: asyncpg.Pool, source: str) -> UUID:
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
            raise RuntimeError("Failed to create ingestion job")
        return UUID(str(row["id"]))


async def _update_job_progress(
    pool: asyncpg.Pool,
    job_id: UUID,
    documents_done: int,
    chunks_created: int,
) -> None:
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
    *, batch_size: int = 64, poll_interval_seconds: float = 1.0
) -> None:
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
            batch_size=batch_size,
            max_retries=cfg.ingestion.embedding.max_retries,
            idle_polls=2,
        )
        await asyncio.sleep(max(0.1, poll_interval_seconds))
