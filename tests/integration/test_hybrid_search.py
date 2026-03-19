"""Integration test — hybrid search (vector + BM25 + RRF) against a populated index."""

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
from cina.serving.search.bm25 import BM25Searcher
from cina.serving.search.fusion import reciprocal_rank_fusion
from cina.serving.search.vector import VectorSearcher

DEFAULT_DSN = "postgresql://cina:cina_dev@localhost:5432/cina"

_DIM = 512


async def _db_available(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn)
    except Exception:
        return False
    await conn.close()
    return True


def _fake_embedding(seed: float) -> list[float]:
    """Generate a deterministic pseudo-embedding for testing."""
    import math

    return [math.sin(seed + i) * 0.5 for i in range(_DIM)]


@pytest.mark.asyncio
async def test_hybrid_search_returns_results_from_both_paths() -> None:
    """Verify vector+BM25 both return results and RRF fuses them."""
    dsn = os.getenv("DATABASE_URL", DEFAULT_DSN)
    if not await _db_available(dsn):
        pytest.skip("Postgres is not reachable for integration test")

    os.environ["DATABASE_URL"] = dsn
    clear_config_cache()
    await close_pool()
    await run_migrations()
    pool = await get_pool()

    doc_repo = DocumentRepository(pool)
    chunk_repo = ChunkRepository(pool)

    # Insert a document with chunks that have known content and embeddings
    doc_id = uuid4()
    section_id = uuid4()
    ingestion_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ingestion_jobs (id, source) VALUES ($1, $2::source_type)",
            ingestion_id,
            "pubmed",
        )
    doc = Document(
        id=doc_id,
        source="pubmed",
        source_id="PMC_HYBRID_TEST",
        title="Metformin Contraindications Study",
        authors=["TestAuthor"],
        publication_date=date(2024, 1, 1),
        raw_metadata={"abstract": "Study of metformin contraindications in renal patients"},
    )
    actual_doc_id = await doc_repo.upsert_document(doc, ingestion_id)
    section = Section(
        id=section_id,
        document_id=actual_doc_id,
        section_type="results",
        heading="Results",
        content="Metformin is contraindicated in patients with severe renal impairment.",
        order=0,
    )
    await doc_repo.replace_sections(actual_doc_id, [section])

    # Create chunks with embeddings
    chunks_data = [
        ("Metformin is contraindicated in patients with eGFR below 30", 0.1),
        ("Renal function should be assessed before initiating metformin therapy", 0.2),
        ("Drug interactions may occur with contrast dye and metformin", 0.3),
    ]
    chunks = []
    chunk_ids = []
    embeddings = []
    for i, (content, seed) in enumerate(chunks_data):
        cid = uuid4()
        chunk_ids.append(str(cid))
        embeddings.append(_fake_embedding(seed))
        chunks.append(
            Chunk(
                id=cid,
                section_id=section_id,
                document_id=actual_doc_id,
                content=content,
                content_hash=f"hybrid_test_{i}",
                token_count=len(content.split()),
                chunk_index=i,
                overlap_tokens=0,
                embedding_model="test-model",
                embedding_dim=_DIM,
                metadata={
                    "source": "pubmed",
                    "source_id": "PMC_HYBRID_TEST",
                    "title": "Metformin Contraindications Study",
                    "section_type": "results",
                },
            )
        )

    await chunk_repo.bulk_upsert(chunks)
    await chunk_repo.update_embeddings(
        chunk_ids,
        embeddings,
        embedding_model="test-model",
        embedding_dim=_DIM,
    )

    # --- Vector search ---
    query_embedding = _fake_embedding(0.15)  # close to first chunk's seed
    vsearcher = VectorSearcher(pool, ef_search=100)
    vector_results = await vsearcher.search(query_embedding, top_k=10)
    assert len(vector_results) > 0, "Vector search should return results"

    # --- BM25 search ---
    bm25searcher = BM25Searcher(pool)
    bm25_results = await bm25searcher.search("metformin contraindicated", top_k=10)
    assert len(bm25_results) > 0, "BM25 search should return results for keyword 'metformin'"

    # --- RRF fusion ---
    fused = reciprocal_rank_fusion(vector_results, bm25_results, k=60)
    assert len(fused) > 0, "Fused results should not be empty"

    # Verify content from our test chunks appears
    fused_contents = [r.content for r in fused]
    assert any("metformin" in c.lower() for c in fused_contents)

    await close_pool()
