from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cina.db.repositories.query_log import QueryLogInsert
from cina.models.provider import CompletionConfig, Message, StreamChunk
from cina.models.search import SearchResult
from cina.serving.pipeline import ServingPipeline


class StubEmbedder:
    async def embed(self, _query: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class FailingEmbedder:
    async def embed(self, _query: str) -> list[float]:
        raise RuntimeError("embed failed")


class StubSearcher:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results

    async def search(self, *_args, **_kwargs) -> list[SearchResult]:
        return self.results


class StubProvider:
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens + output_tokens) / 1000


class StubQueryLogRepo:
    def __init__(self) -> None:
        self.calls: list[QueryLogInsert] = []

    async def insert(self, entry: QueryLogInsert) -> None:
        self.calls.append(entry)


class StubCostTracker:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def log_event(self, event: object) -> None:
        self.calls.append(event)


async def _passthrough_keepalive(stream, _interval: int):
    async for item in stream:
        yield item


def _make_result(content: str) -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        content=content,
        token_count=12,
        metadata={"source": "pubmed", "title": "Study"},
        score=0.9,
    )


def _build_pipeline() -> ServingPipeline:
    pipeline = ServingPipeline.__new__(ServingPipeline)
    pipeline.embedder = StubEmbedder()
    pipeline.vector_searcher = StubSearcher([_make_result("vector chunk")])
    pipeline.bm25_searcher = StubSearcher([])
    pipeline.reranker = None
    pipeline.provider = StubProvider()
    pipeline.prompt_router = None
    pipeline.query_log_repo = StubQueryLogRepo()
    pipeline.cost_tracker = StubCostTracker()
    pipeline.vector_top_k = 50
    pipeline.bm25_top_k = 50
    pipeline.rrf_k = 60
    pipeline.rerank_candidates = 10
    pipeline.max_chunks = 5
    pipeline.generation_buffer = 128
    pipeline.keepalive_interval = 60
    pipeline.model_context_limit = 2048
    return pipeline


def _parse_sse(events: list[str]) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    for raw in events:
        lines = [line for line in raw.strip().splitlines() if line]
        event_name = lines[0].split(": ", maxsplit=1)[1]
        data = json.loads(lines[1].split(": ", maxsplit=1)[1])
        parsed.append((event_name, data))
    return parsed


@pytest.mark.asyncio
async def test_stream_query_emits_metadata_tokens_citations_metrics_and_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _build_pipeline()

    monkeypatch.setattr("cina.serving.pipeline.merge_with_keepalive", _passthrough_keepalive)
    monkeypatch.setattr(
        "cina.serving.pipeline.assemble_context",
        lambda _results, _budget: [
            SimpleNamespace(number=1, content="ctx", metadata={"source": "pubmed"}),
        ],
    )
    monkeypatch.setattr(
        "cina.serving.pipeline.build_citations",
        lambda _sources: [{"source": "pubmed", "index": 1}],
    )
    monkeypatch.setattr(
        "cina.serving.pipeline.build_messages",
        lambda _query, _sources, _system: [
            Message(role="system", content="sys"),
            Message(role="user", content="user"),
        ],
    )
    monkeypatch.setattr("cina.serving.pipeline.count_tokens", lambda text: len(text))

    async def handler(messages: list[Message], config: CompletionConfig):
        _ = messages
        config.metadata["provider_model"] = "gpt-4o"
        config.metadata["provider_used"] = "openai"
        config.metadata["cache_hit"] = False
        config.metadata["output_tokens"] = 2
        config.metadata["estimated_cost_usd"] = 0.004
        config.metadata["cost_event"] = {
            "query_id": config.metadata["query_id"],
            "tenant_id": "tenant-x",
            "provider": "openai",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 2,
            "estimated_cost_usd": 0.004,
            "cache_hit": False,
        }
        yield StreamChunk(text="A")
        yield StreamChunk(text="B")

    pipeline.handler = handler

    events = [item async for item in pipeline.stream_query("What is HER2?", tenant_id="tenant-x")]
    parsed = _parse_sse(events)
    event_names = [event_name for event_name, _ in parsed]

    assert event_names == ["metadata", "token", "token", "citations", "metrics", "done"]
    assert any(
        payload.get("provider") == "openai" for name, payload in parsed if name == "metadata"
    )
    assert len(pipeline.query_log_repo.calls) == 1
    assert len(pipeline.cost_tracker.calls) == 1


@pytest.mark.asyncio
async def test_stream_query_preprocessing_failure_returns_error_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _build_pipeline()
    pipeline.embedder = FailingEmbedder()
    pipeline.handler = lambda _m, _c: (_ for _ in ())

    monkeypatch.setattr("cina.serving.pipeline.merge_with_keepalive", _passthrough_keepalive)

    events = [item async for item in pipeline.stream_query("broken query")]
    parsed = _parse_sse(events)

    assert [name for name, _ in parsed] == ["metadata", "token", "citations", "metrics", "done"]
    assert parsed[1][1]["text"] == "[Error: query preprocessing failed]"
