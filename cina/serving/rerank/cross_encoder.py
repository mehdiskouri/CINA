"""Cross-encoder re-ranker using sentence-transformers on GPU/CPU."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import TYPE_CHECKING

from cina.models.search import SearchResult
from cina.observability.logging import get_logger
from cina.observability.metrics import cina_rerank_latency_seconds

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

log = get_logger("cina.serving.rerank.cross_encoder")


class CrossEncoderReranker:
    """Loads a cross-encoder model once and re-ranks candidates for each query."""

    def __init__(self, model_name: str, *, device: str = "auto", top_n: int = 10) -> None:
        self.model_name = model_name
        self.top_n = top_n
        self._device = self._resolve_device(device)
        self._model: CrossEncoder | None = None

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _get_model(self) -> CrossEncoder:
        if self._model is None:
            from sentence_transformers import CrossEncoder as _CE

            log.info("loading_cross_encoder", model=self.model_name, device=self._device)
            self._model = _CE(self.model_name, device=self._device)
        return self._model

    def _predict_sync(self, query: str, candidates: list[SearchResult]) -> list[SearchResult]:
        model = self._get_model()
        pairs = [(query, c.content) for c in candidates]
        scores = model.predict(pairs)
        scored = list(zip(candidates, scores, strict=True))
        scored.sort(key=lambda x: float(x[1]), reverse=True)
        return [
            SearchResult(
                chunk_id=item.chunk_id,
                content=item.content,
                token_count=item.token_count,
                metadata=item.metadata,
                score=float(score),
            )
            for item, score in scored[: self.top_n]
        ]

    async def rerank(self, query: str, candidates: list[SearchResult]) -> list[SearchResult]:
        if not candidates:
            return []

        start = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None, functools.partial(self._predict_sync, query, candidates)
            )
        finally:
            elapsed = time.perf_counter() - start
            cina_rerank_latency_seconds.observe(elapsed)

        log.debug(
            "rerank_complete",
            candidates=len(candidates),
            returned=len(results),
            elapsed_ms=round(elapsed * 1000, 1),
        )
        return results

    def warmup(self) -> None:
        """Pre-load model weights (call at app startup)."""
        self._get_model()
        log.info("cross_encoder_warmed_up", model=self.model_name, device=self._device)
