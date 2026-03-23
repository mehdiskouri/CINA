"""Fusion helpers for combining ranking outputs from multiple retrievers."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from cina.models.search import SearchResult


def reciprocal_rank_fusion(*result_lists: list[SearchResult], k: int = 60) -> list[SearchResult]:
    """Merge ranked lists using reciprocal-rank fusion (RRF)."""
    scores: dict[UUID, float] = defaultdict(float)
    result_map: dict[UUID, SearchResult] = {}

    for result_list in result_lists:
        for rank, result in enumerate(result_list):
            scores[result.chunk_id] += 1.0 / (k + rank + 1)
            result_map[result.chunk_id] = result

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [result_map[chunk_id] for chunk_id, _ in ranked]
