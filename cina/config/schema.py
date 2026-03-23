"""Typed configuration schema models for CINA runtime settings."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChunkConfigModel(BaseModel):
    """Chunking controls for ingestion."""

    max_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"
    respect_sections: bool = True
    sentence_alignment: bool = True


class EmbeddingConfigModel(BaseModel):
    """Embedding provider and batching settings."""

    provider: str = "openai"
    model: str = "text-embedding-3-large"
    dimensions: int = 512
    batch_size: int = 64
    max_retries: int = 3
    rate_limit_tpm: int = 1_000_000


class QueueConfigModel(BaseModel):
    """Queue backend settings for ingestion workers."""

    backend: str = "redis"
    concurrency: int = 8
    name: str = "cina:queue:ingestion"
    sqs_url_env: str = "SQS_QUEUE_URL"
    sqs_dlq_url_env: str = "SQS_DLQ_URL"
    sqs_region_env: str = "AWS_REGION"
    sqs_endpoint_url_env: str = "AWS_SQS_ENDPOINT_URL"


class SourceToggleModel(BaseModel):
    """Per-source toggle and connector-specific options."""

    enabled: bool = True
    data_dir: str | None = None
    api_base: str | None = None


class SourcesConfigModel(BaseModel):
    """Configuration for all ingestion data sources."""

    pubmed: SourceToggleModel = Field(
        default_factory=lambda: SourceToggleModel(data_dir="/data/pubmed"),
    )
    fda: SourceToggleModel = Field(default_factory=lambda: SourceToggleModel(data_dir="/data/fda"))
    clinicaltrials: SourceToggleModel = Field(
        default_factory=lambda: SourceToggleModel(api_base="https://clinicaltrials.gov/api/v2"),
    )


class IngestionConfigModel(BaseModel):
    """Top-level ingestion subsystem settings."""

    chunk: ChunkConfigModel = Field(default_factory=ChunkConfigModel)
    embedding: EmbeddingConfigModel = Field(default_factory=EmbeddingConfigModel)
    queue: QueueConfigModel = Field(default_factory=QueueConfigModel)
    sources: SourcesConfigModel = Field(default_factory=SourcesConfigModel)


class SearchConfigModel(BaseModel):
    """Hybrid search retrieval tuning parameters."""

    vector_top_k: int = 50
    bm25_top_k: int = 50
    rrf_k: int = 60
    ef_search: int = 100


class RerankConfigModel(BaseModel):
    """Cross-encoder reranking settings."""

    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    candidates: int = 20
    top_n: int = 10
    device: str = "auto"


class ContextConfigModel(BaseModel):
    """Context window budgeting parameters."""

    max_chunks: int = 15
    generation_buffer_tokens: int = 2048


class StreamConfigModel(BaseModel):
    """SSE streaming behavior settings."""

    keepalive_interval_seconds: int = 15


class ServingConfigModel(BaseModel):
    """Top-level serving/query pipeline settings."""

    search: SearchConfigModel = Field(default_factory=SearchConfigModel)
    rerank: RerankConfigModel = Field(default_factory=RerankConfigModel)
    context: ContextConfigModel = Field(default_factory=ContextConfigModel)
    stream: StreamConfigModel = Field(default_factory=StreamConfigModel)


class ProviderConfigModel(BaseModel):
    """LLM provider connection and timeout settings."""

    name: str
    model: str
    api_key_env: str
    timeout_connect: float = 5.0
    timeout_read: float = 60.0


class ProvidersConfigModel(BaseModel):
    """Primary and fallback provider selection defaults."""

    primary: ProviderConfigModel = Field(
        default_factory=lambda: ProviderConfigModel(
            name="anthropic",
            model="claude-sonnet-4-20250514",
            api_key_env="ANTHROPIC_API_KEY",
        ),
    )
    fallback: ProviderConfigModel = Field(
        default_factory=lambda: ProviderConfigModel(
            name="openai",
            model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
        ),
    )


class FallbackConfigModel(BaseModel):
    """Fallback trigger and circuit-breaker settings."""

    ttft_threshold_seconds: float = 5.0
    circuit_breaker_failures: int = 3
    circuit_breaker_cooldown: int = 60


class CacheConfigModel(BaseModel):
    """Semantic cache controls."""

    enabled: bool = True
    num_hyperplanes: int = 16
    similarity_threshold: float = 0.95
    ttl_seconds: int = 86_400


class RateLimitConfigModel(BaseModel):
    """Tenant request/token rate-limit settings."""

    requests_per_minute: int = 100
    tokens_per_hour: int = 100_000


class PromptConfigModel(BaseModel):
    """Prompt version routing defaults."""

    default_version: str = "v1.0"


class OrchestrationConfigModel(BaseModel):
    """Top-level orchestration middleware and routing settings."""

    providers: ProvidersConfigModel = Field(default_factory=ProvidersConfigModel)
    fallback: FallbackConfigModel = Field(default_factory=FallbackConfigModel)
    cache: CacheConfigModel = Field(default_factory=CacheConfigModel)
    rate_limit: RateLimitConfigModel = Field(default_factory=RateLimitConfigModel)
    prompt: PromptConfigModel = Field(default_factory=PromptConfigModel)


class PostgresConfigModel(BaseModel):
    """PostgreSQL connection pool settings."""

    dsn_env: str = "DATABASE_URL"
    pool_min: int = 5
    pool_max: int = 20


class RedisConfigModel(BaseModel):
    """Redis client settings."""

    url_env: str = "REDIS_URL"
    pool_max: int = 20


class DatabaseConfigModel(BaseModel):
    """Database subsystem settings for Postgres and Redis."""

    postgres: PostgresConfigModel = Field(default_factory=PostgresConfigModel)
    redis: RedisConfigModel = Field(default_factory=RedisConfigModel)


class ObservabilityConfigModel(BaseModel):
    """Logging and metrics endpoint settings."""

    log_level: str = "INFO"
    log_format: str = "json"
    metrics_port: int = 9090
    prometheus_path: str = "/metrics"


class AppConfig(BaseSettings):
    """Runtime configuration after file and environment resolution."""

    model_config = SettingsConfigDict(
        env_prefix="CINA__",
        env_nested_delimiter="__",
        extra="ignore",
        validate_default=True,
    )

    ingestion: IngestionConfigModel = Field(default_factory=IngestionConfigModel)
    serving: ServingConfigModel = Field(default_factory=ServingConfigModel)
    orchestration: OrchestrationConfigModel = Field(default_factory=OrchestrationConfigModel)
    database: DatabaseConfigModel = Field(default_factory=DatabaseConfigModel)
    observability: ObservabilityConfigModel = Field(default_factory=ObservabilityConfigModel)


class FileConfig(BaseModel):
    """Config file schema before environment overlay."""

    model_config = ConfigDict(extra="ignore")
    ingestion: IngestionConfigModel = Field(default_factory=IngestionConfigModel)
    serving: ServingConfigModel = Field(default_factory=ServingConfigModel)
    orchestration: OrchestrationConfigModel = Field(default_factory=OrchestrationConfigModel)
    database: DatabaseConfigModel = Field(default_factory=DatabaseConfigModel)
    observability: ObservabilityConfigModel = Field(default_factory=ObservabilityConfigModel)
