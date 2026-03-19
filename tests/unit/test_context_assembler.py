"""Unit tests for context assembler — budget packing and citation extraction."""

from uuid import uuid4

from cina.models.search import SearchResult
from cina.serving.context.assembler import (
    ContextBudget,
    assemble_context,
    build_citations,
)


def _make_chunk(token_count: int = 100, **meta_overrides: object) -> SearchResult:
    meta: dict[str, object] = {
        "source": "pubmed",
        "source_id": "PMC123",
        "title": "Test Article",
        "section_type": "methods",
        "authors": ["Smith J"],
        "publication_date": "2024-01-01",
    }
    meta.update(meta_overrides)
    return SearchResult(
        chunk_id=uuid4(),
        content="x " * (token_count // 2),
        token_count=token_count,
        metadata=meta,
        score=0.9,
    )


def _budget(available: int, max_chunks: int = 15) -> ContextBudget:
    """Shorthand to create a budget with a specific available amount."""
    return ContextBudget(
        model_context_limit=available + 500,
        system_prompt_tokens=200,
        query_tokens=100,
        generation_buffer=200,
        max_chunks=max_chunks,
    )


class TestContextBudget:
    def test_available_calculation(self) -> None:
        b = ContextBudget(
            model_context_limit=128_000,
            system_prompt_tokens=500,
            query_tokens=50,
            generation_buffer=2048,
        )
        assert b.available == 128_000 - 500 - 50 - 2048

    def test_available_floors_at_zero(self) -> None:
        b = ContextBudget(
            model_context_limit=100,
            system_prompt_tokens=500,
            query_tokens=50,
            generation_buffer=2048,
        )
        assert b.available == 0


class TestAssembleContext:
    def test_all_chunks_fit(self) -> None:
        chunks = [_make_chunk(100) for _ in range(3)]
        budget = _budget(500)
        sources = assemble_context(chunks, budget)
        assert len(sources) == 3
        assert [s.index for s in sources] == [1, 2, 3]

    def test_over_budget_skips_chunks(self) -> None:
        chunks = [_make_chunk(100) for _ in range(5)]
        budget = _budget(250)
        sources = assemble_context(chunks, budget)
        # Only 2 fit (100+100=200 <= 250, 100+100+100=300 > 250)
        assert len(sources) == 2

    def test_skip_and_try_large_chunk_skipped_smaller_fits(self) -> None:
        chunks = [
            _make_chunk(100),  # fits
            _make_chunk(300),  # too large, skipped
            _make_chunk(50),  # fits after skip
        ]
        budget = _budget(200)
        sources = assemble_context(chunks, budget)
        assert len(sources) == 2
        assert sources[0].chunk.token_count == 100
        assert sources[1].chunk.token_count == 50

    def test_max_chunks_cap(self) -> None:
        chunks = [_make_chunk(10) for _ in range(20)]
        budget = _budget(10000, max_chunks=5)
        sources = assemble_context(chunks, budget)
        assert len(sources) == 5

    def test_zero_budget_returns_empty(self) -> None:
        chunks = [_make_chunk(100)]
        budget = _budget(0)
        sources = assemble_context(chunks, budget)
        assert sources == []

    def test_single_chunk_larger_than_budget_returns_empty(self) -> None:
        chunks = [_make_chunk(500)]
        budget = _budget(100)
        sources = assemble_context(chunks, budget)
        assert sources == []

    def test_empty_chunks_returns_empty(self) -> None:
        budget = _budget(10000)
        assert assemble_context([], budget) == []

    def test_indexes_are_one_based_sequential(self) -> None:
        chunks = [_make_chunk(10) for _ in range(4)]
        budget = _budget(10000)
        sources = assemble_context(chunks, budget)
        assert [s.index for s in sources] == [1, 2, 3, 4]


class TestBuildCitations:
    def test_citation_metadata_extracted(self) -> None:
        chunks = [_make_chunk(100, source="fda", source_id="FDA001", title="Drug Label")]
        budget = _budget(10000)
        sources = assemble_context(chunks, budget)
        citations = build_citations(sources)
        assert len(citations) == 1
        c = citations[0]
        assert c["index"] == 1
        assert c["source"] == "fda"
        assert c["source_id"] == "FDA001"
        assert c["document_title"] == "Drug Label"

    def test_empty_sources_returns_empty_citations(self) -> None:
        assert build_citations([]) == []
