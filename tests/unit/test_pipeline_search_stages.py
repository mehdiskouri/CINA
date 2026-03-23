from __future__ import annotations

from uuid import uuid4

import pytest

from cina.models.search import SearchResult
from cina.serving.pipeline import ServingPipeline


class StubSearcher:
    def __init__(self, result: list[SearchResult] | Exception) -> None:
        self._result = result

    async def search(self, *_args, **_kwargs) -> list[SearchResult]:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class StubReranker:
    def __init__(self, *, result: list[SearchResult] | None = None, fail: bool = False) -> None:
        self.result = result
        self.fail = fail

    async def rerank(self, _query: str, candidates: list[SearchResult]) -> list[SearchResult]:
        if self.fail:
            raise RuntimeError("rerank failed")
        return self.result if self.result is not None else candidates


def _sr(score: float, content: str) -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        content=content,
        token_count=20,
        metadata={"source": "pubmed"},
        score=score,
    )


def _build_pipeline() -> ServingPipeline:
    pipeline = ServingPipeline.__new__(ServingPipeline)
    pipeline.vector_top_k = 50
    pipeline.bm25_top_k = 50
    pipeline.rrf_k = 60
    pipeline.rerank_candidates = 3
    pipeline.reranker = None
    return pipeline


@pytest.mark.asyncio
async def test_safe_search_returns_empty_on_exception() -> None:
    pipeline = _build_pipeline()

    async def _boom() -> list[SearchResult]:
        raise RuntimeError("search failed")

    out = await pipeline._safe_search("vector", _boom())
    assert out == []


@pytest.mark.asyncio
async def test_hybrid_search_returns_single_non_empty_list() -> None:
    pipeline = _build_pipeline()
    vector_only = [_sr(0.9, "vector")]
    pipeline.vector_searcher = StubSearcher(vector_only)
    pipeline.bm25_searcher = StubSearcher([])

    out = await pipeline._hybrid_search("query", [0.1, 0.2])

    assert out == vector_only


@pytest.mark.asyncio
async def test_hybrid_search_fuses_and_limits_candidates() -> None:
    pipeline = _build_pipeline()
    vector = [_sr(0.9, "a"), _sr(0.8, "b"), _sr(0.7, "c")]
    bm25 = [_sr(1.0, "d"), _sr(0.6, "e"), _sr(0.5, "f")]
    pipeline.vector_searcher = StubSearcher(vector)
    pipeline.bm25_searcher = StubSearcher(bm25)

    out = await pipeline._hybrid_search("query", [0.1, 0.2])

    assert len(out) == 3


@pytest.mark.asyncio
async def test_rerank_returns_candidates_when_reranker_unset_or_fails() -> None:
    pipeline = _build_pipeline()
    candidates = [_sr(0.5, "x"), _sr(0.4, "y")]

    no_reranker_out = await pipeline._rerank("q", candidates)
    assert no_reranker_out == candidates

    pipeline.reranker = StubReranker(fail=True)
    failed_out = await pipeline._rerank("q", candidates)
    assert failed_out == candidates


@pytest.mark.asyncio
async def test_rerank_uses_reranker_output() -> None:
    pipeline = _build_pipeline()
    candidates = [_sr(0.2, "x"), _sr(0.3, "y")]
    reranked = list(reversed(candidates))
    pipeline.reranker = StubReranker(result=reranked)

    out = await pipeline._rerank("q", candidates)
    assert out == reranked
