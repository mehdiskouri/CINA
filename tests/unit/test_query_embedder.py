from __future__ import annotations

from types import SimpleNamespace

import pytest

from cina.serving.search.embed import QueryEmbedder


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, int]] = []

    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        self.calls.append((texts, model, dimensions))
        return [[0.1, 0.2, 0.3]]


@pytest.mark.asyncio
async def test_query_embedder_uses_configured_model_and_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cina.serving.search.embed.load_config",
        lambda: SimpleNamespace(
            ingestion=SimpleNamespace(
                embedding=SimpleNamespace(model="embed-model", dimensions=384),
            ),
        ),
    )

    provider = FakeEmbeddingProvider()
    embedder = QueryEmbedder(provider=provider)

    vector = await embedder.embed("clinical question")

    assert vector == [0.1, 0.2, 0.3]
    assert provider.calls == [(["clinical question"], "embed-model", 384)]


class ExplodingProvider:
    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        _ = (texts, model, dimensions)
        raise RuntimeError("embedding failed")


@pytest.mark.asyncio
async def test_query_embedder_propagates_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cina.serving.search.embed.load_config",
        lambda: SimpleNamespace(
            ingestion=SimpleNamespace(
                embedding=SimpleNamespace(model="embed-model", dimensions=512),
            ),
        ),
    )

    embedder = QueryEmbedder(provider=ExplodingProvider())

    with pytest.raises(RuntimeError, match="embedding failed"):
        await embedder.embed("x")
