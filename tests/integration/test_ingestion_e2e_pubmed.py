from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

from cina.cli.db import run_migrations
from cina.config import clear_config_cache
from cina.db.connection import close_pool
from cina.ingestion.pipeline import run_ingestion

DEFAULT_DSN = "postgresql://cina:cina_dev@localhost:5432/cina"


class FakeQueue:
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.messages: list[dict[str, object]] = []

    async def enqueue(self, message: dict[str, object], queue_name: str) -> str:
        self.messages.append({**message, "_queue": queue_name})
        return str(len(self.messages))

    async def dequeue(self, queue_name: str, wait_timeout_seconds: int) -> dict[str, object] | None:
        for idx, message in enumerate(self.messages):
            if message.get("_queue") == queue_name:
                queued = self.messages.pop(idx)
                queued["__receipt"] = f"{queue_name}|{idx}"
                return queued
        return None

    async def acknowledge(self, receipt: str) -> None:
        return None

    async def dead_letter(self, message: dict[str, object], queue_name: str, reason: str) -> None:
        return None


class FakeEmbeddingProvider:
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        return None

    async def embed(self, texts: list[str], model: str, dimensions: int) -> list[list[float]]:
        return [[0.001 * (idx + 1) for _ in range(dimensions)] for idx, _ in enumerate(texts)]

    async def health_check(self) -> bool:
        return True


def _write_pubmed_file(path: Path, pmcid: str, section_type: str) -> None:
    xml = f"""
<article>
  <front>
    <article-meta>
      <article-id pub-id-type='pmcid'>{pmcid}</article-id>
      <title-group><article-title>Title {pmcid}</article-title></title-group>
      <contrib-group>
        <contrib contrib-type='author'>
          <name><given-names>Jane</given-names><surname>Doe</surname></name>
        </contrib>
      </contrib-group>
      <pub-date><year>2024</year><month>1</month><day>2</day></pub-date>
    </article-meta>
  </front>
  <body>
    <sec sec-type='{section_type}'>
      <title>Section</title>
      <p>Sentence one for {pmcid}. Sentence two for {pmcid}. Sentence three for {pmcid}.</p>
    </sec>
  </body>
</article>
"""
    path.write_text(xml, encoding="utf-8")


async def _db_available(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn)
    except Exception:
        return False
    await conn.close()
    return True


@pytest.mark.asyncio
async def test_ingestion_e2e_pubmed_50_docs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dsn = os.getenv("DATABASE_URL", DEFAULT_DSN)
    if not await _db_available(dsn):
        pytest.skip("Postgres is not reachable for integration test")

    monkeypatch.setenv("DATABASE_URL", dsn)
    clear_config_cache()
    await close_pool()

    monkeypatch.setattr("cina.ingestion.pipeline.RedisStreamQueue", FakeQueue)
    monkeypatch.setattr("cina.ingestion.pipeline.OpenAIEmbeddingProvider", FakeEmbeddingProvider)

    data_dir = tmp_path / "pubmed"
    data_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(50):
        _write_pubmed_file(
            data_dir / f"PMC{idx:06d}.xml",
            pmcid=f"PMC{idx:06d}",
            section_type="methods" if idx % 2 == 0 else "results",
        )

    await run_migrations()

    conn = await asyncpg.connect(dsn)
    await conn.execute("TRUNCATE chunks, sections, documents, ingestion_jobs RESTART IDENTITY CASCADE")
    await conn.close()

    result_1 = await run_ingestion(
        source="pubmed",
        path=data_dir,
        limit=50,
        concurrency=8,
        batch_size=16,
    )

    conn = await asyncpg.connect(dsn)
    docs_count = await conn.fetchval("SELECT count(*) FROM documents")
    sections_count = await conn.fetchval("SELECT count(*) FROM sections")
    chunks_count = await conn.fetchval("SELECT count(*) FROM chunks")
    embedded_count = await conn.fetchval("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
    dims_ok = await conn.fetchval("SELECT count(*) FROM chunks WHERE embedding_dim = 512")

    lineage_row = await conn.fetchrow(
        """
        SELECT c.id AS chunk_id, s.id AS section_id, d.id AS document_id
        FROM chunks c
        JOIN sections s ON s.id = c.section_id
        JOIN documents d ON d.id = c.document_id
        LIMIT 1
        """
    )
    await conn.close()

    assert result_1.documents_processed == 50
    assert result_1.errors == []
    assert docs_count == 50
    assert sections_count >= 50
    assert chunks_count > 0
    assert embedded_count == chunks_count
    assert dims_ok == chunks_count
    assert lineage_row is not None
    assert lineage_row["chunk_id"] is not None

    result_2 = await run_ingestion(
        source="pubmed",
        path=data_dir,
        limit=50,
        concurrency=8,
        batch_size=16,
    )

    conn = await asyncpg.connect(dsn)
    chunks_count_after = await conn.fetchval("SELECT count(*) FROM chunks")
    await conn.close()

    assert result_2.errors == []
    assert chunks_count_after == chunks_count
