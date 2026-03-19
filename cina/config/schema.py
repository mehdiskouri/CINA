from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChunkConfigModel(BaseModel):
    max_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"
    respect_sections: bool = True
    sentence_alignment: bool = True


class EmbeddingConfigModel(BaseModel):
    provider: str = "openai"
    model: str = "text-embedding-3-large"
    dimensions: int = 512
    batch_size: int = 64
    max_retries: int = 3
    rate_limit_tpm: int = 1_000_000


class QueueConfigModel(BaseModel):
    backend: str = "redis"
    concurrency: int = 8


class SourceToggleModel(BaseModel):
    enabled: bool = True
    data_dir: str | None = None
    api_base: str | None = None


class SourcesConfigModel(BaseModel):
    pubmed: SourceToggleModel = Field(default_factory=lambda: SourceToggleModel(data_dir="/data/pubmed"))
    fda: SourceToggleModel = Field(default_factory=lambda: SourceToggleModel(data_dir="/data/fda"))
    clinicaltrials: SourceToggleModel = Field(
        default_factory=lambda: SourceToggleModel(api_base="https://clinicaltrials.gov/api/v2")
    )


class IngestionConfigModel(BaseModel):
    chunk: ChunkConfigModel = Field(default_factory=ChunkConfigModel)
    embedding: EmbeddingConfigModel = Field(default_factory=EmbeddingConfigModel)
    queue: QueueConfigModel = Field(default_factory=QueueConfigModel)
    sources: SourcesConfigModel = Field(default_factory=SourcesConfigModel)


class SearchConfigModel(BaseModel):
    vector_top_k: int = 50
    bm25_top_k: int = 50
    rrf_k: int = 60
    ef_search: int = 100


class RerankConfigModel(BaseModel):
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    candidates: int = 20
    top_n: int = 10
    device: str = "auto"


class ContextConfigModel(BaseModel):
    max_chunks: int = 15
    generation_buffer_tokens: int = 2048


class StreamConfigModel(BaseModel):
    keepalive_interval_seconds: int = 15


class ServingConfigModel(BaseModel):
    search: SearchConfigModel = Field(default_factory=SearchConfigModel)
    rerank: RerankConfigModel = Field(default_factory=RerankConfigModel)
    context: ContextConfigModel = Field(default_factory=ContextConfigModel)
    stream: StreamConfigModel = Field(default_factory=StreamConfigModel)


class ProviderConfigModel(BaseModel):
    name: str
    model: str
    api_key_env: str
    timeout_connect: float = 5.0
    timeout_read: float = 60.0


class ProvidersConfigModel(BaseModel):
    primary: ProviderConfigModel = Field(
        default_factory=lambda: ProviderConfigModel(
            name="anthropic",
            model="claude-sonnet-4-20250514",
            api_key_env="ANTHROPIC_API_KEY",
        )
    )
    fallback: ProviderConfigModel = Field(
        default_factory=lambda: ProviderConfigModel(
            name="openai",
            model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
        )
    )


class FallbackConfigModel(BaseModel):
    ttft_threshold_seconds: float = 5.0
    circuit_breaker_failures: int = 3
    circuit_breaker_cooldown: int = 60


class CacheConfigModel(BaseModel):
    enabled: bool = True
    num_hyperplanes: int = 16
    similarity_threshold: float = 0.95
    ttl_seconds: int = 86_400


class RateLimitConfigModel(BaseModel):
    requests_per_minute: int = 100
    tokens_per_hour: int = 100_000


class PromptConfigModel(BaseModel):
    default_version: str = "v1.0"


class OrchestrationConfigModel(BaseModel):
    providers: ProvidersConfigModel = Field(default_factory=ProvidersConfigModel)
    fallback: FallbackConfigModel = Field(default_factory=FallbackConfigModel)
    cache: CacheConfigModel = Field(default_factory=CacheConfigModel)
    rate_limit: RateLimitConfigModel = Field(default_factory=RateLimitConfigModel)
    prompt: PromptConfigModel = Field(default_factory=PromptConfigModel)


class PostgresConfigModel(BaseModel):
    dsn_env: str = "DATABASE_URL"
    pool_min: int = 5
    pool_max: int = 20


class RedisConfigModel(BaseModel):
    url_env: str = "REDIS_URL"
    pool_max: int = 20


class DatabaseConfigModel(BaseModel):
    postgres: PostgresConfigModel = Field(default_factory=PostgresConfigModel)
    redis: RedisConfigModel = Field(default_factory=RedisConfigModel)


class ObservabilityConfigModel(BaseModel):
    log_level: str = "INFO"
    log_format: str = "json"
    metrics_port: int = 9090
    prometheus_path: str = "/metrics"


class AppConfig(BaseSettings):
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
    model_config = ConfigDict(extra="ignore")
    ingestion: IngestionConfigModel = Field(default_factory=IngestionConfigModel)
    serving: ServingConfigModel = Field(default_factory=ServingConfigModel)
    orchestration: OrchestrationConfigModel = Field(default_factory=OrchestrationConfigModel)
    database: DatabaseConfigModel = Field(default_factory=DatabaseConfigModel)
    observability: ObservabilityConfigModel = Field(default_factory=ObservabilityConfigModel)
