"""Unit tests for cross-encoder reranker."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest

from cina.models.search import SearchResult
from cina.serving.rerank.cross_encoder import CrossEncoderReranker


def _make_result(content: str, score: float = 0.5) -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        content=content,
        token_count=20,
        metadata={"source": "pubmed"},
        score=score,
    )


class TestCrossEncoderReranker:
    def test_device_resolution_auto_no_cuda(self) -> None:
        with patch("torch.cuda.is_available", return_value=False):
            assert CrossEncoderReranker._resolve_device("auto") == "cpu"

    def test_device_resolution_explicit(self) -> None:
        assert CrossEncoderReranker._resolve_device("cpu") == "cpu"

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self) -> None:
        reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
        reranker.model_name = "test"
        reranker.top_n = 5
        reranker._device = "cpu"
        reranker._model = None
        result = await reranker.rerank("query", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_reranking_changes_order(self) -> None:
        """With a mock model, verify that cross-encoder scores reorder results."""
        reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
        reranker.model_name = "test"
        reranker.top_n = 3
        reranker._device = "cpu"

        # Mock model that assigns scores in reverse order of input
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([0.1, 0.9, 0.5])
        reranker._model = mock_model

        candidates = [
            _make_result("low relevance", score=0.95),  # RRF rank 1
            _make_result("high relevance", score=0.8),  # RRF rank 2
            _make_result("medium relevance", score=0.7),  # RRF rank 3
        ]

        result = await reranker.rerank("test query", candidates)

        # After reranking: high(0.9) > medium(0.5) > low(0.1)
        assert result[0].content == "high relevance"
        assert result[1].content == "medium relevance"
        assert result[2].content == "low relevance"

    @pytest.mark.asyncio
    async def test_top_n_limits_output(self) -> None:
        reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
        reranker.model_name = "test"
        reranker.top_n = 2
        reranker._device = "cpu"

        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([0.3, 0.9, 0.5, 0.1])
        reranker._model = mock_model

        candidates = [_make_result(f"chunk-{i}") for i in range(4)]
        result = await reranker.rerank("query", candidates)
        assert len(result) == 2

    def test_warmup_calls_get_model(self) -> None:
        reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
        reranker.model_name = "test"
        reranker._device = "cpu"
        reranker._model = MagicMock()  # already loaded
        reranker.warmup()  # should not error
