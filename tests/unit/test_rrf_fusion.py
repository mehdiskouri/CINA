from __future__ import annotations

from uuid import uuid4

from cina.models.search import SearchResult
from cina.serving.search.fusion import reciprocal_rank_fusion


def _result(chunk_id, score: float) -> SearchResult:  # type: ignore[no-untyped-def]
    return SearchResult(
        chunk_id=chunk_id,
        content="content",
        token_count=10,
        metadata={"source": "pubmed"},
        score=score,
    )


def test_rrf_prioritizes_items_present_in_multiple_lists() -> None:
    a = uuid4()
    b = uuid4()
    c = uuid4()

    list_1 = [_result(a, 0.9), _result(b, 0.8)]
    list_2 = [_result(c, 0.95), _result(a, 0.7)]

    ranked = reciprocal_rank_fusion(list_1, list_2, k=60)

    assert ranked[0].chunk_id == a


def test_rrf_is_stable_for_single_input_list() -> None:
    a = uuid4()
    b = uuid4()
    list_1 = [_result(a, 0.8), _result(b, 0.7)]

    ranked = reciprocal_rank_fusion(list_1, k=60)

    assert [item.chunk_id for item in ranked] == [a, b]


def test_rrf_handles_empty_lists() -> None:
    ranked = reciprocal_rank_fusion([], [], k=60)

    assert ranked == []
