from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import asyncpg
import pytest

from cina.cli.db import run_migrations
from cina.config import clear_config_cache
from cina.db.connection import close_pool, get_pool
from cina.db.repositories.chunk import ChunkRepository
from cina.db.repositories.document import DocumentRepository
from cina.models.document import Chunk, Document, Section

DEFAULT_DSN = "postgresql://cina:cina_dev@localhost:5432/cina"


async def _db_available(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn)
    except Exception:
        return False
    await conn.close()
    return True


@pytest.mark.asyncio
async def test_document_and_chunk_repositories() -> None:
    dsn = os.getenv("DATABASE_URL", DEFAULT_DSN)
    if not await _db_available(dsn):
        pytest.skip("Postgres is not reachable for integration test")

    os.environ["DATABASE_URL"] = dsn
    clear_config_cache()
    await close_pool()
    await run_migrations()

    pool = await get_pool()
    document_repo = DocumentRepository(pool)
    chunk_repo = ChunkRepository(pool)

    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE chunks, sections, documents, ingestion_jobs RESTART IDENTITY CASCADE")
        ingestion_id = await conn.fetchval(
            "INSERT INTO ingestion_jobs(source, status, started_at) VALUES ('pubmed', 'running', now()) RETURNING id"
        )

    doc = Document(
        id=uuid4(),
        source="pubmed",
        source_id="PMC-REPO-1",
        title="Repository Test",
        authors=["Author"],
        publication_date=date(2024, 1, 1),
        sections=[],
    )

    doc_id = await document_repo.upsert_document(doc, ingestion_id=ingestion_id)

    section = Section(
        id=uuid4(),
        document_id=doc_id,
        section_type="abstract",
        heading="Abstract",
        content="Therapy improves outcomes in controlled trial.",
        order=0,
    )
    inserted_sections = await document_repo.replace_sections(doc_id, [section])

    chunk = Chunk(
        id=uuid4(),
        section_id=section.id,
        document_id=doc_id,
        content="Therapy improves outcomes in controlled trial.",
        content_hash="repo-hash-1",
        token_count=7,
        chunk_index=0,
        embedding_model="text-embedding-3-large",
        embedding_dim=512,
        metadata={"source": "pubmed"},
    )

    inserted_chunks = await chunk_repo.bulk_upsert([chunk])
    assert inserted_sections == 1
    assert inserted_chunks == 1

    await chunk_repo.update_embeddings(
        [str(chunk.id)],
        [[0.1 for _ in range(512)]],
        embedding_model="text-embedding-3-large",
        embedding_dim=512,
    )

    vector_hits = await chunk_repo.vector_search([0.1 for _ in range(512)], top_k=5)
    bm25_hits = await chunk_repo.bm25_search("therapy outcomes trial", top_k=5)
    by_ids = await chunk_repo.get_by_ids([chunk.id])

    assert len(vector_hits) >= 1
    assert len(bm25_hits) >= 1
    assert len(by_ids) == 1
    assert by_ids[0].content_hash == "repo-hash-1"
