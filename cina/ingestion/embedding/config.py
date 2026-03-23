"""Configuration model for embedding provider settings."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbedConfig:
    """Embedding model, sizing, and retry controls."""

    provider: str = "openai"
    model: str = "text-embedding-3-large"
    dimensions: int = 512
    batch_size: int = 64
    max_retries: int = 3
