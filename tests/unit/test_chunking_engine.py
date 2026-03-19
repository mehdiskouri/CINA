from datetime import date
from uuid import uuid4

from cina.ingestion.chunking.config import ChunkConfig
from cina.ingestion.chunking.engine import ChunkingEngine
from cina.models.document import Document, Section


def _build_document(content: str) -> Document:
    doc_id = uuid4()
    return Document(
        id=doc_id,
        source="pubmed",
        source_id="pmc-test",
        title="Test document",
        authors=["A. Author"],
        publication_date=date(2024, 1, 1),
        sections=[
            Section(
                id=uuid4(),
                document_id=doc_id,
                section_type="abstract",
                heading="Abstract",
                content=content,
                order=0,
            )
        ],
    )


def test_chunking_engine_splits_long_text() -> None:
    content = " ".join(["sentence"] * 400)
    document = _build_document(content)
    engine = ChunkingEngine(
        ChunkConfig(
            max_chunk_tokens=40,
            overlap_tokens=8,
            sentence_boundary_alignment=False,
        )
    )

    chunks = engine.chunk_document(document, embedding_model="text-embedding-3-small")

    assert len(chunks) > 1
    assert all(chunk.embedding_model == "text-embedding-3-small" for chunk in chunks)
    assert chunks[0].chunk_index == 0
    assert all(chunk.token_count > 0 for chunk in chunks)


def test_chunking_engine_preserves_section_metadata() -> None:
    document = _build_document("First sentence. Second sentence. Third sentence.")
    engine = ChunkingEngine(
        ChunkConfig(
            max_chunk_tokens=30,
            overlap_tokens=4,
            sentence_boundary_alignment=True,
        )
    )

    chunks = engine.chunk_document(document, embedding_model="text-embedding-3-small")

    assert len(chunks) >= 1
    assert chunks[0].metadata["section_type"] == "abstract"
    assert chunks[0].metadata["source"] == "pubmed"
