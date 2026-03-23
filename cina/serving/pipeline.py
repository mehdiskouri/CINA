"""Query serving pipeline — end-to-end: embed → search → rerank → assemble → LLM → SSE."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from cina.config import load_config
from cina.db.repositories.cost_event import CostEventInsert
from cina.db.repositories.query_log import QueryLogInsert
from cina.models.provider import CompletionConfig, Message
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
from cina.serving.search.bm25 import BM25Searcher
from cina.serving.search.embed import QueryEmbedder
from cina.serving.search.fusion import reciprocal_rank_fusion
from cina.serving.search.vector import VectorSearcher
from cina.serving.stream.sse import merge_with_keepalive, sse_event

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable

    import asyncpg

    from cina.db.repositories.query_log import QueryLogRepository
    from cina.models.provider import StreamChunk
    from cina.models.search import SearchResult
    from cina.orchestration.limits.cost_tracker import CostTracker
    from cina.orchestration.middleware import Handler
    from cina.orchestration.providers.protocol import LLMProviderProtocol
    from cina.orchestration.routing.prompt_router import PromptRouter
    from cina.serving.rerank.cross_encoder import CrossEncoderReranker

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


@dataclass(slots=True)
class ServingPipelineDependencies:
    """Optional collaborators used by the serving pipeline."""

    reranker: CrossEncoderReranker | None = None
    embedder: QueryEmbedder | None = None
    provider: LLMProviderProtocol | None = None
    handler: Handler | None = None
    prompt_router: PromptRouter | None = None
    query_log_repo: QueryLogRepository | None = None
    cost_tracker: CostTracker | None = None


@dataclass(slots=True)
class PreparedQuery:
    """Preprocessed query artifacts needed during streaming."""

    fused: list[SearchResult]
    query_embedding: list[float]
    prompt_version: str
    messages: list[Message]


class ServingPipeline:
    """Orchestrates the full query → SSE response flow."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        dependencies: ServingPipelineDependencies | None = None,
    ) -> None:
        """Initialize pipeline dependencies and serving configuration."""
        cfg = load_config()
        scfg = cfg.serving
        pcfg = cfg.orchestration.providers.primary
        deps = dependencies or ServingPipelineDependencies()

        self.pool = pool
        self.vector_searcher = VectorSearcher(pool, ef_search=scfg.search.ef_search)
        self.bm25_searcher = BM25Searcher(pool)
        self.embedder = deps.embedder or QueryEmbedder()
        self.reranker = deps.reranker
        self.provider = deps.provider or AnthropicProvider(
            model=pcfg.model,
            api_key_env=pcfg.api_key_env,
            timeout_connect=pcfg.timeout_connect,
            timeout_read=pcfg.timeout_read,
        )
        self.handler = deps.handler or self.provider.complete
        self.prompt_router = deps.prompt_router
        self.query_log_repo = deps.query_log_repo
        self.cost_tracker = deps.cost_tracker

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

        try:
            prepared = await self._prepare_query(result)
        except Exception:
            log.exception("query_preprocessing_failed", query_id=result.query_id)
            async for event in merge_with_keepalive(
                self._preprocessing_error_stream(result.query_id),
                self.keepalive_interval,
            ):
                yield event
            return

        async for event in merge_with_keepalive(
            self._event_stream(result, prepared, tenant_id),
            self.keepalive_interval,
        ):
            yield event

    async def _prepare_query(self, result: PipelineResult) -> PreparedQuery:
        query = result.query
        t0 = time.perf_counter()
        query_embedding = await self.embedder.embed(query)
        result.timings.embed_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        fused = await self._hybrid_search(query, query_embedding)
        result.timings.search_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        reranked = await self._rerank(query, fused)
        result.timings.rerank_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        prompt_version = "v1.0"
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
        result.input_tokens = sum(count_tokens(message.content) for message in messages)
        result.timings.assembly_ms = (time.perf_counter() - t0) * 1000

        return PreparedQuery(
            fused=fused,
            query_embedding=query_embedding,
            prompt_version=prompt_version,
            messages=messages,
        )

    async def _event_stream(
        self,
        result: PipelineResult,
        prepared: PreparedQuery,
        tenant_id: str | None,
    ) -> AsyncIterator[str]:
        config = self._build_completion_config(result, prepared, tenant_id)

        async for event in self._stream_llm_events(result, prepared, config):
            yield event

        citations = config.metadata.get("cached_citations", result.citations)
        yield sse_event("citations", {"citations": citations})

        metrics_payload = self._build_metrics_payload(result, config)
        yield sse_event("metrics", metrics_payload)
        yield sse_event("done", {})

        total_ms = self._record_total_latency(result)
        await self._persist_query_log(result, prepared, config, tenant_id, total_ms)
        await self._persist_cost_event(result, config)

    def _build_completion_config(
        self,
        result: PipelineResult,
        prepared: PreparedQuery,
        tenant_id: str | None,
    ) -> CompletionConfig:
        return CompletionConfig(
            max_tokens=1024,
            temperature=0.3,
            metadata={
                "query_id": result.query_id,
                "tenant_id": tenant_id,
                "query_embedding": prepared.query_embedding,
                "prompt_version": prepared.prompt_version,
                "input_tokens": result.input_tokens,
                "citations": result.citations,
            },
        )

    async def _stream_llm_events(
        self,
        result: PipelineResult,
        prepared: PreparedQuery,
        config: CompletionConfig,
    ) -> AsyncIterator[str]:
        llm_start = time.perf_counter()
        first_token = True

        try:
            token_iter = self.handler(prepared.messages, config).__aiter__()
            first_chunk, has_first_chunk = await self._read_first_chunk(token_iter)
            if has_first_chunk:
                result.timings.llm_ttft_ms = (time.perf_counter() - llm_start) * 1000
                first_token = False

            yield sse_event(
                "metadata",
                self._metadata_payload(result, prepared, config, provider_override=None),
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
                self._metadata_payload(result, prepared, config, provider_override="unknown"),
            )
            yield sse_event("token", {"text": "[Error: LLM provider unavailable]"})

        result.timings.llm_total_ms = (time.perf_counter() - llm_start) * 1000

    async def _read_first_chunk(
        self,
        token_iter: AsyncIterator[StreamChunk],
    ) -> tuple[StreamChunk | None, bool]:
        try:
            return (await anext(token_iter), True)
        except StopAsyncIteration:
            return (None, False)

    def _metadata_payload(
        self,
        result: PipelineResult,
        prepared: PreparedQuery,
        config: CompletionConfig,
        *,
        provider_override: str | None,
    ) -> dict[str, object]:
        provider = (
            provider_override
            if provider_override is not None
            else str(config.metadata.get("provider_used", "anthropic"))
        )
        cache_hit = (
            False
            if provider_override is not None
            else bool(config.metadata.get("cache_hit", False))
        )
        return {
            "query_id": result.query_id,
            "model": str(config.metadata.get("provider_model", "unknown")),
            "provider": provider,
            "sources_used": len(result.sources),
            "cache_hit": cache_hit,
            "prompt_version": prepared.prompt_version,
        }

    def _build_metrics_payload(
        self,
        result: PipelineResult,
        config: CompletionConfig,
    ) -> dict[str, object]:
        estimated_cost_raw = config.metadata.get(
            "estimated_cost_usd",
            self.provider.estimate_cost(result.input_tokens, result.output_tokens),
        )
        estimated_cost = (
            float(estimated_cost_raw) if isinstance(estimated_cost_raw, (int, float, str)) else 0.0
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
        return metrics_payload

    def _record_total_latency(self, result: PipelineResult) -> float:
        total_ms = (
            result.timings.embed_ms
            + result.timings.search_ms
            + result.timings.rerank_ms
            + result.timings.assembly_ms
            + result.timings.llm_total_ms
        )
        cina_query_latency_seconds.labels(stage="total").observe(total_ms / 1000)
        return total_ms

    async def _persist_query_log(
        self,
        result: PipelineResult,
        prepared: PreparedQuery,
        config: CompletionConfig,
        tenant_id: str | None,
        total_ms: float,
    ) -> None:
        if self.query_log_repo is None:
            return

        try:
            await self.query_log_repo.insert(
                QueryLogInsert(
                    query_id=result.query_id,
                    query_text=result.query,
                    prompt_version_id=prepared.prompt_version,
                    provider_used=str(config.metadata.get("provider_used", "unknown")),
                    fallback_triggered=bool(config.metadata.get("fallback_triggered", False)),
                    cache_hit=bool(config.metadata.get("cache_hit", False)),
                    total_latency_ms=int(total_ms),
                    search_latency_ms=int(result.timings.search_ms),
                    rerank_latency_ms=int(result.timings.rerank_ms),
                    llm_latency_ms=int(result.timings.llm_total_ms),
                    chunks_retrieved=len(prepared.fused),
                    chunks_used=len(result.sources),
                    tenant_id=tenant_id,
                ),
            )
        except Exception:
            log.exception("query_log_insert_failed", query_id=result.query_id)

    async def _persist_cost_event(
        self,
        result: PipelineResult,
        config: CompletionConfig,
    ) -> None:
        cost_event = config.metadata.get("cost_event")
        if self.cost_tracker is None or not isinstance(cost_event, dict):
            return

        try:
            await self.cost_tracker.log_event(
                CostEventInsert(
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
                ),
            )
        except Exception:
            log.exception("cost_event_insert_failed", query_id=result.query_id)

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
            self._safe_search("vector", self.vector_searcher.search(embedding, self.vector_top_k)),
        )
        bm25_task = asyncio.create_task(
            self._safe_search("bm25", self.bm25_searcher.search(query, self.bm25_top_k)),
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
