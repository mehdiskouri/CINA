from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from cina.ingestion.chunking.config import ChunkConfig
from cina.ingestion.chunking.engine import ChunkingEngine
from cina.models.document import Document, Section


def _build_document(content: str) -> Document:
    section = Section(
        id=uuid4(),
        document_id=uuid4(),
        section_type="body",
        heading="h",
        content=content,
        order=0,
        created_at=datetime.now(UTC),
    )
    return Document(
        id=uuid4(),
        source="pubmed",
        source_id="pm-1",
        title="Trial",
        authors=["A"],
        raw_metadata={"k": "v"},
        sections=[section],
    )


def test_chunk_document_short_section_returns_single_chunk() -> None:
    engine = ChunkingEngine(ChunkConfig(max_chunk_tokens=512, overlap_tokens=32))
    doc = _build_document("Short sentence.")

    chunks = engine.chunk_document(doc, embedding_model="m")

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].metadata["source"] == "pubmed"


def test_chunk_document_sentence_boundary_path_creates_multiple_chunks() -> None:
    engine = ChunkingEngine(
        ChunkConfig(
            max_chunk_tokens=25,
            overlap_tokens=8,
            sentence_boundary_alignment=True,
        )
    )
    text = " ".join([f"Sentence {idx}." for idx in range(1, 30)])
    doc = _build_document(text)

    chunks = engine.chunk_document(doc, embedding_model="m")

    assert len(chunks) >= 2
    assert all(chunk.embedding_model == "m" for chunk in chunks)
    assert chunks[0].overlap_tokens in (0, 8)


def test_chunk_document_token_window_path_when_sentence_alignment_disabled() -> None:
    engine = ChunkingEngine(
        ChunkConfig(
            max_chunk_tokens=20,
            overlap_tokens=5,
            sentence_boundary_alignment=False,
        )
    )
    text = " ".join(["word"] * 160)
    doc = _build_document(text)

    chunks = engine.chunk_document(doc, embedding_model="m")

    assert len(chunks) > 1
    assert chunks[0].overlap_tokens == 0
    assert all(chunk.token_count <= 20 for chunk in chunks)


def test_token_windows_overlap_sentences_and_overlap_estimation_helpers() -> None:
    engine = ChunkingEngine(ChunkConfig(max_chunk_tokens=12, overlap_tokens=4))

    windows = engine._token_windows(" ".join(["x"] * 50), max_tokens=12, overlap_tokens=4)
    overlap = engine._overlap_sentences(["a b c", "d e", "f g"])
    estimated = engine._estimate_overlap_tokens(overlap)

    assert len(windows) >= 2
    assert estimated <= 4


def test_build_chunk_normalizes_content_and_sets_hash() -> None:
    engine = ChunkingEngine(ChunkConfig(max_chunk_tokens=50, overlap_tokens=5))
    doc = _build_document("abc")
    section = doc.sections[0]

    chunk = engine._build_chunk(doc, section, "  padded text  ", 2, 3, "model-x")

    assert chunk.content == "padded text"
    assert chunk.chunk_index == 2
    assert chunk.overlap_tokens == 3
    assert len(chunk.content_hash) == 64
