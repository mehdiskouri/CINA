"""Query serving pipeline — end-to-end: embed → search → rerank → assemble → LLM → SSE."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from uuid import uuid4

import asyncpg

from cina.config import load_config
from cina.models.provider import CompletionConfig
from cina.models.search import SearchResult
from cina.observability.logging import get_logger
from cina.observability.metrics import cina_query_latency_seconds
from cina.orchestration.providers.anthropic import AnthropicProvider
from cina.serving.context.assembler import (
    ContextBudget,
    NumberedSource,
    assemble_context,
    build_citations,
    count_tokens,
)
from cina.serving.context.prompt import CLINICAL_SYSTEM_PROMPT, build_messages
from cina.serving.rerank.cross_encoder import CrossEncoderReranker
from cina.serving.search.bm25 import BM25Searcher
from cina.serving.search.embed import QueryEmbedder
from cina.serving.search.fusion import reciprocal_rank_fusion
from cina.serving.search.vector import VectorSearcher
from cina.serving.stream.sse import merge_with_keepalive, sse_event

log = get_logger("cina.serving.pipeline")


@dataclass(slots=True)
class StageTimings:
    """Collects per-stage latency in milliseconds."""

    embed_ms: float = 0.0
    search_ms: float = 0.0
    rerank_ms: float = 0.0
    assembly_ms: float = 0.0
    llm_ttft_ms: float = 0.0
    llm_total_ms: float = 0.0


@dataclass(slots=True)
class PipelineResult:
    """Intermediate result passed through the pipeline stages."""

    query_id: str = ""
    query: str = ""
    sources: list[NumberedSource] = field(default_factory=list)
    citations: list[dict[str, object]] = field(default_factory=list)
    timings: StageTimings = field(default_factory=StageTimings)
    input_tokens: int = 0
    output_tokens: int = 0


class ServingPipeline:
    """Orchestrates the full query → SSE response flow."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        reranker: CrossEncoderReranker | None = None,
        embedder: QueryEmbedder | None = None,
        provider: AnthropicProvider | None = None,
    ) -> None:
        cfg = load_config()
        scfg = cfg.serving
        pcfg = cfg.orchestration.providers.primary

        self.pool = pool
        self.vector_searcher = VectorSearcher(pool, ef_search=scfg.search.ef_search)
        self.bm25_searcher = BM25Searcher(pool)
        self.embedder = embedder or QueryEmbedder()
        self.reranker = reranker
        self.provider = provider or AnthropicProvider(
            model=pcfg.model,
            api_key_env=pcfg.api_key_env,
            timeout_connect=pcfg.timeout_connect,
            timeout_read=pcfg.timeout_read,
        )

        # Config values
        self.vector_top_k = scfg.search.vector_top_k
        self.bm25_top_k = scfg.search.bm25_top_k
        self.rrf_k = scfg.search.rrf_k
        self.rerank_candidates = scfg.rerank.candidates
        self.max_chunks = scfg.context.max_chunks
        self.generation_buffer = scfg.context.generation_buffer_tokens
        self.keepalive_interval = scfg.stream.keepalive_interval_seconds

        # Model context limits (Claude Sonnet 4)
        self.model_context_limit = 200_000

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def stream_query(self, query: str) -> AsyncIterator[str]:
        """Full pipeline: returns an async iterator of SSE-formatted strings."""
        result = PipelineResult(query_id=str(uuid4()), query=query)

        # 1. Embed query
        t0 = time.perf_counter()
        query_embedding = await self.embedder.embed(query)
        result.timings.embed_ms = (time.perf_counter() - t0) * 1000

        # 2. Hybrid search (parallel vector + BM25, graceful degradation)
        t0 = time.perf_counter()
        fused = await self._hybrid_search(query, query_embedding)
        result.timings.search_ms = (time.perf_counter() - t0) * 1000

        # 3. Rerank (graceful degradation: skip if unavailable/failing)
        t0 = time.perf_counter()
        reranked = await self._rerank(query, fused)
        result.timings.rerank_ms = (time.perf_counter() - t0) * 1000

        # 4. Context assembly
        t0 = time.perf_counter()
        system_prompt = CLINICAL_SYSTEM_PROMPT
        budget = ContextBudget(
            model_context_limit=self.model_context_limit,
            system_prompt_tokens=count_tokens(system_prompt),
            query_tokens=count_tokens(query),
            generation_buffer=self.generation_buffer,
            max_chunks=self.max_chunks,
        )
        result.sources = assemble_context(reranked, budget)
        result.citations = build_citations(result.sources)
        messages = build_messages(query, result.sources, system_prompt)
        result.input_tokens = sum(count_tokens(m.content) for m in messages)
        result.timings.assembly_ms = (time.perf_counter() - t0) * 1000

        # 5. Stream SSE events
        async def _event_stream() -> AsyncIterator[str]:
            # metadata event
            yield sse_event(
                "metadata",
                {
                    "query_id": result.query_id,
                    "model": self.provider.model,
                    "provider": "anthropic",
                    "sources_used": len(result.sources),
                    "cache_hit": False,
                },
            )

            # token events from LLM
            config = CompletionConfig(max_tokens=1024, temperature=0.3)
            llm_start = time.perf_counter()
            first_token = True

            try:
                token_stream = self.provider.complete(messages, config)
                async for chunk in token_stream:
                    if first_token:
                        result.timings.llm_ttft_ms = (time.perf_counter() - llm_start) * 1000
                        first_token = False
                    result.output_tokens += len(chunk.text)
                    yield sse_event("token", {"text": chunk.text})
            except Exception:
                log.exception("llm_stream_error", query_id=result.query_id)
                yield sse_event("token", {"text": "[Error: LLM provider unavailable]"})

            result.timings.llm_total_ms = (time.perf_counter() - llm_start) * 1000

            # citations event
            yield sse_event("citations", {"citations": result.citations})

            # metrics event
            estimated_cost = self.provider.estimate_cost(result.input_tokens, result.output_tokens)
            yield sse_event(
                "metrics",
                {
                    "search_latency_ms": round(result.timings.search_ms, 1),
                    "rerank_latency_ms": round(result.timings.rerank_ms, 1),
                    "assembly_latency_ms": round(result.timings.assembly_ms, 1),
                    "llm_ttft_ms": round(result.timings.llm_ttft_ms, 1),
                    "llm_total_ms": round(result.timings.llm_total_ms, 1),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "estimated_cost_usd": round(estimated_cost, 6),
                },
            )

            # done event
            yield sse_event("done", {})

            # Record overall latency
            total_ms = (
                result.timings.embed_ms
                + result.timings.search_ms
                + result.timings.rerank_ms
                + result.timings.assembly_ms
                + result.timings.llm_total_ms
            )
            cina_query_latency_seconds.labels(stage="total").observe(total_ms / 1000)

        # Wrap with keepalive
        async for event in merge_with_keepalive(_event_stream(), self.keepalive_interval):
            yield event

    # ------------------------------------------------------------------
    # Internal stages
    # ------------------------------------------------------------------

    async def _hybrid_search(
        self,
        query: str,
        embedding: list[float],
    ) -> list[SearchResult]:
        """Run vector + BM25 in parallel with graceful degradation."""
        vector_task = asyncio.create_task(
            self._safe_search("vector", self.vector_searcher.search(embedding, self.vector_top_k))
        )
        bm25_task = asyncio.create_task(
            self._safe_search("bm25", self.bm25_searcher.search(query, self.bm25_top_k))
        )

        vector_results, bm25_results = await asyncio.gather(vector_task, bm25_task)

        # Graceful degradation
        if not vector_results and not bm25_results:
            log.warning("both_search_paths_empty", query=query[:80])
            return []

        result_lists = [r for r in [vector_results, bm25_results] if r]
        if len(result_lists) == 1:
            return result_lists[0]

        fused = reciprocal_rank_fusion(*result_lists, k=self.rrf_k)
        return fused[: self.rerank_candidates]

    async def _safe_search(
        self,
        name: str,
        coro: Awaitable[list[SearchResult]],
    ) -> list[SearchResult]:
        """Execute a search coroutine, returning empty list on failure."""
        try:
            return await coro
        except Exception:
            log.exception("search_path_failed", path=name)
            return []

    async def _rerank(
        self,
        query: str,
        candidates: list[SearchResult],
    ) -> list[SearchResult]:
        """Rerank candidates, falling back to original order on failure."""
        if self.reranker is None:
            return candidates
        try:
            return await self.reranker.rerank(query, candidates)
        except Exception:
            log.exception("rerank_failed_graceful_degradation")
            return candidates
