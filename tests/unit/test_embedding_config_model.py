from __future__ import annotations

from cina.ingestion.embedding.config import EmbedConfig


def test_embed_config_defaults() -> None:
    cfg = EmbedConfig()
    assert cfg.provider == "openai"
    assert cfg.model == "text-embedding-3-large"
    assert cfg.dimensions == 512
    assert cfg.batch_size == 64
    assert cfg.max_retries == 3
