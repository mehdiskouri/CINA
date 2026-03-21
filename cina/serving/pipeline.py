"""Query serving pipeline — end-to-end: embed → search → rerank → assemble → LLM → SSE."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

import asyncpg

from cina.config import load_config
from cina.models.provider import CompletionConfig, Message
from cina.models.search import SearchResult
from cina.observability.logging import get_logger
from cina.observability.metrics import cina_query_latency_seconds
from cina.orchestration.limits.cost_tracker import CostTracker
from cina.orchestration.middleware import Handler
from cina.orchestration.providers.anthropic import AnthropicProvider
from cina.orchestration.providers.protocol import LLMProviderProtocol
from cina.orchestration.routing.prompt_router import PromptRouter
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

if TYPE_CHECKING:
    from cina.db.repositories.query_log import QueryLogRepository

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
        provider: LLMProviderProtocol | None = None,
        handler: Handler | None = None,
        prompt_router: PromptRouter | None = None,
        query_log_repo: QueryLogRepository | None = None,
        cost_tracker: CostTracker | None = None,
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
        self.handler = handler or self.provider.complete
        self.prompt_router = prompt_router
        self.query_log_repo = query_log_repo
        self.cost_tracker = cost_tracker

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

    async def stream_query(self, query: str, *, tenant_id: str | None = None) -> AsyncIterator[str]:
        """Full pipeline: returns an async iterator of SSE-formatted strings."""
        result = PipelineResult(query_id=str(uuid4()), query=query)
        fused: list[SearchResult] = []
        prompt_version = "v1.0"
        messages: list[Message] = []
        query_embedding: list[float] = []

        try:
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
            if self.prompt_router is not None:
                choice = await self.prompt_router.choose()
                prompt_version = choice.version_id
                system_prompt = choice.system_prompt

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
        except Exception:
            log.exception("query_preprocessing_failed", query_id=result.query_id)
            async for event in merge_with_keepalive(
                self._preprocessing_error_stream(result.query_id),
                self.keepalive_interval,
            ):
                yield event
            return

        # 5. Stream SSE events
        async def _event_stream() -> AsyncIterator[str]:
            config = CompletionConfig(
                max_tokens=1024,
                temperature=0.3,
                metadata={
                    "query_id": result.query_id,
                    "tenant_id": tenant_id,
                    "query_embedding": query_embedding,
                    "prompt_version": prompt_version,
                    "input_tokens": result.input_tokens,
                    "citations": result.citations,
                },
            )
            llm_start = time.perf_counter()
            first_token = True
            first_chunk = None

            try:
                token_iter = self.handler(messages, config).__aiter__()
                try:
                    first_chunk = await anext(token_iter)
                    result.timings.llm_ttft_ms = (time.perf_counter() - llm_start) * 1000
                    first_token = False
                except StopAsyncIteration:
                    first_chunk = None

                yield sse_event(
                    "metadata",
                    {
                        "query_id": result.query_id,
                        "model": str(config.metadata.get("provider_model", "unknown")),
                        "provider": str(config.metadata.get("provider_used", "anthropic")),
                        "sources_used": len(result.sources),
                        "cache_hit": bool(config.metadata.get("cache_hit", False)),
                        "prompt_version": prompt_version,
                    },
                )

                if first_chunk is not None:
                    result.output_tokens += len(first_chunk.text)
                    yield sse_event("token", {"text": first_chunk.text})

                async for chunk in token_iter:
                    if first_token:
                        result.timings.llm_ttft_ms = (time.perf_counter() - llm_start) * 1000
                        first_token = False
                    result.output_tokens += len(chunk.text)
                    yield sse_event("token", {"text": chunk.text})
            except Exception:
                log.exception("llm_stream_error", query_id=result.query_id)
                yield sse_event(
                    "metadata",
                    {
                        "query_id": result.query_id,
                        "model": str(config.metadata.get("provider_model", "unknown")),
                        "provider": "unknown",
                        "sources_used": len(result.sources),
                        "cache_hit": False,
                        "prompt_version": prompt_version,
                    },
                )
                yield sse_event("token", {"text": "[Error: LLM provider unavailable]"})

            result.timings.llm_total_ms = (time.perf_counter() - llm_start) * 1000

            # citations event
            citations = config.metadata.get("cached_citations", result.citations)
            yield sse_event("citations", {"citations": citations})

            # metrics event
            estimated_cost_raw = config.metadata.get(
                "estimated_cost_usd",
                self.provider.estimate_cost(result.input_tokens, result.output_tokens),
            )
            estimated_cost = (
                float(estimated_cost_raw)
                if isinstance(estimated_cost_raw, (int, float, str))
                else 0.0
            )
            output_tokens_raw = config.metadata.get("output_tokens", result.output_tokens)
            output_tokens = (
                int(output_tokens_raw)
                if isinstance(output_tokens_raw, (int, float, str))
                else result.output_tokens
            )
            metrics_payload: dict[str, object] = {
                "search_latency_ms": round(result.timings.search_ms, 1),
                "rerank_latency_ms": round(result.timings.rerank_ms, 1),
                "assembly_latency_ms": round(result.timings.assembly_ms, 1),
                "llm_ttft_ms": round(result.timings.llm_ttft_ms, 1),
                "llm_total_ms": round(result.timings.llm_total_ms, 1),
                "input_tokens": result.input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": round(estimated_cost, 6),
            }
            config.metadata["metrics_payload"] = metrics_payload
            yield sse_event(
                "metrics",
                metrics_payload,
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

            if self.query_log_repo is not None:
                try:
                    await self.query_log_repo.insert(
                        query_id=result.query_id,
                        query_text=query,
                        prompt_version_id=prompt_version,
                        provider_used=str(config.metadata.get("provider_used", "unknown")),
                        fallback_triggered=bool(config.metadata.get("fallback_triggered", False)),
                        cache_hit=bool(config.metadata.get("cache_hit", False)),
                        total_latency_ms=int(total_ms),
                        search_latency_ms=int(result.timings.search_ms),
                        rerank_latency_ms=int(result.timings.rerank_ms),
                        llm_latency_ms=int(result.timings.llm_total_ms),
                        chunks_retrieved=len(fused),
                        chunks_used=len(result.sources),
                        tenant_id=tenant_id,
                    )
                except Exception:
                    log.exception("query_log_insert_failed", query_id=result.query_id)

            cost_event = config.metadata.get("cost_event")
            if self.cost_tracker is not None and isinstance(cost_event, dict):
                try:
                    await self.cost_tracker.log_event(
                        query_id=str(cost_event["query_id"]),
                        tenant_id=cost_event["tenant_id"]
                        if isinstance(cost_event.get("tenant_id"), str)
                        else None,
                        provider=str(cost_event["provider"]),
                        model=str(cost_event["model"]),
                        input_tokens=int(cost_event["input_tokens"]),
                        output_tokens=int(cost_event["output_tokens"]),
                        estimated_cost_usd=float(cost_event["estimated_cost_usd"]),
                        cache_hit=bool(cost_event["cache_hit"]),
                    )
                except Exception:
                    log.exception("cost_event_insert_failed", query_id=result.query_id)

        # Wrap with keepalive
        async for event in merge_with_keepalive(_event_stream(), self.keepalive_interval):
            yield event

    async def _preprocessing_error_stream(self, query_id: str) -> AsyncIterator[str]:
        yield sse_event(
            "metadata",
            {
                "query_id": query_id,
                "model": "unavailable",
                "provider": "unavailable",
                "sources_used": 0,
                "cache_hit": False,
                "prompt_version": "unknown",
            },
        )
        yield sse_event("token", {"text": "[Error: query preprocessing failed]"})
        yield sse_event("citations", {"citations": []})
        yield sse_event(
            "metrics",
            {
                "search_latency_ms": 0.0,
                "rerank_latency_ms": 0.0,
                "assembly_latency_ms": 0.0,
                "llm_ttft_ms": 0.0,
                "llm_total_ms": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
            },
        )
        yield sse_event("done", {})

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
