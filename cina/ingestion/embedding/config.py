from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbedConfig:
    provider: str = "openai"
    model: str = "text-embedding-3-large"
    dimensions: int = 512
    batch_size: int = 64
    max_retries: int = 3
