"""Integration test — end-to-end query via the FastAPI app with mocked LLM."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from cina.cli.db import run_migrations
from cina.config import clear_config_cache
from cina.db.connection import close_pool, get_pool
from cina.db.repositories.chunk import ChunkRepository
from cina.db.repositories.document import DocumentRepository
from cina.models.document import Chunk, Document, Section
from cina.models.provider import StreamChunk

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
    import math

    return [math.sin(seed + i) * 0.5 for i in range(_DIM)]


async def _seed_data(pool: asyncpg.Pool) -> None:
    """Insert test documents/chunks with embeddings."""
    doc_repo = DocumentRepository(pool)
    chunk_repo = ChunkRepository(pool)

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
        source_id="PMC_E2E_TEST",
        title="E2E Test Article on Metformin",
        authors=["E2E Author"],
        publication_date=date(2024, 6, 1),
        raw_metadata={"abstract": "End to end test for query pipeline"},
    )
    actual_doc_id = await doc_repo.upsert_document(doc, ingestion_id)
    section = Section(
        id=section_id,
        document_id=actual_doc_id,
        section_type="results",
        heading="Results",
        content="Metformin reduces HbA1c levels by approximately 1.5%.",
        order=0,
    )
    await doc_repo.replace_sections(actual_doc_id, [section])

    chunks = []
    chunk_ids = []
    embeddings = []
    for i, content in enumerate(
        [
            "Metformin reduces HbA1c levels by approximately 1.5% in type 2 diabetes.",
            "Gastrointestinal side effects are the most common adverse events with metformin.",
        ]
    ):
        cid = uuid4()
        chunk_ids.append(str(cid))
        embeddings.append(_fake_embedding(i * 0.1))
        chunks.append(
            Chunk(
                id=cid,
                section_id=section_id,
                document_id=actual_doc_id,
                content=content,
                content_hash=f"e2e_test_{i}",
                token_count=len(content.split()),
                chunk_index=i,
                overlap_tokens=0,
                embedding_model="test-model",
                embedding_dim=_DIM,
                metadata={
                    "source": "pubmed",
                    "source_id": "PMC_E2E_TEST",
                    "title": "E2E Test Article on Metformin",
                    "section_type": "results",
                    "authors": ["E2E Author"],
                    "publication_date": "2024-06-01",
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


async def _mock_llm_stream(*args: object, **kwargs: object) -> AsyncIterator[StreamChunk]:
    """Fake LLM that yields a few tokens."""
    for word in ["Based", " on", " the", " sources", "."]:
        yield StreamChunk(text=word)


@pytest.mark.asyncio
async def test_query_e2e_sse_event_sequence() -> None:
    """Full end-to-end: seed DB, query API, verify SSE event order."""
    dsn = os.getenv("DATABASE_URL", DEFAULT_DSN)
    if not await _db_available(dsn):
        pytest.skip("Postgres is not reachable for integration test")

    os.environ["DATABASE_URL"] = dsn
    clear_config_cache()
    await close_pool()
    await run_migrations()
    pool = await get_pool()
    await _seed_data(pool)

    # Build mocks for heavy dependencies
    mock_embedder = MagicMock()
    mock_embedder.embed = AsyncMock(return_value=_fake_embedding(0.05))

    mock_provider = MagicMock()
    mock_provider.model = "test-model"
    mock_provider.complete = _mock_llm_stream
    mock_provider.estimate_cost = MagicMock(return_value=0.001)

    mock_reranker = MagicMock()
    mock_reranker.warmup = MagicMock()
    mock_reranker.rerank = AsyncMock(side_effect=lambda q, c: c[:10])

    # Build the app with a no-op lifespan and manually wire state
    from cina.api.app import create_app
    from cina.serving.pipeline import ServingPipeline

    @asynccontextmanager
    async def _noop_lifespan(_app: object) -> AsyncIterator[None]:
        yield

    with patch("cina.api.app.lifespan", _noop_lifespan):
        app = create_app()

    pipeline = ServingPipeline(
        pool,
        reranker=mock_reranker,
        embedder=mock_embedder,
        provider=mock_provider,
    )
    app.state.serving_pipeline = pipeline
    app.state.reranker = mock_reranker
    app.state.provider = mock_provider
    apikey_repo = MagicMock()
    apikey_repo.validate_token = AsyncMock(
        return_value=SimpleNamespace(tenant_id="test-tenant", name="test-key")
    )
    app.state.apikey_repo = apikey_repo

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/query",
                json={"query": "What are the effects of metformin?"},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer test-token",
                },
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            # Parse SSE events
            events: list[tuple[str, dict[str, object]]] = []
            body = resp.text
            for block in body.split("\n\n"):
                block = block.strip()
                if not block or block.startswith(":"):
                    continue
                lines = block.split("\n")
                event_type = ""
                data = ""
                for line in lines:
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data = line[6:]
                if event_type and data:
                    events.append((event_type, json.loads(data)))

            # Verify event sequence
            event_types = [e[0] for e in events]
            assert event_types[0] == "metadata"
            assert event_types[-1] == "done"
            assert "citations" in event_types
            assert "metrics" in event_types
            assert "token" in event_types

            # Verify metadata has required fields
            meta = events[0][1]
            assert "query_id" in meta
            assert "sources_used" in meta

            # Verify token events
            token_events = [e[1] for e in events if e[0] == "token"]
            assert len(token_events) > 0
            full_text = "".join(str(t.get("text", "")) for t in token_events)
            assert len(full_text) > 0

            # Verify metrics event
            metrics_events = [e[1] for e in events if e[0] == "metrics"]
            assert len(metrics_events) == 1
            m = metrics_events[0]
            assert "search_latency_ms" in m
            assert "rerank_latency_ms" in m
    finally:
        await close_pool()
