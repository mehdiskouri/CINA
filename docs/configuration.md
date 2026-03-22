# CINA Configuration Reference

CINA uses a layered configuration system:

1. **Defaults** — hardcoded in Pydantic models (`cina/config/schema.py`)
2. **YAML file** — `cina.yaml` in the project root
3. **Environment variables** — prefix `CINA__` with `__` as nesting delimiter

Each layer overrides the previous. Environment variables always win.

---

## Annotated `cina.yaml`

```yaml
ingestion:
  chunk:
    max_tokens: 512            # Maximum tokens per chunk
    overlap_tokens: 64         # Token overlap between consecutive chunks
    tokenizer: cl100k_base     # Tiktoken tokenizer (matches OpenAI models)
    respect_sections: true     # Never split chunks across section boundaries
    sentence_alignment: true   # Align chunk boundaries to sentence endings

  embedding:
    provider: openai           # Embedding provider
    model: text-embedding-3-large
    dimensions: 512            # Output embedding dimensions
    batch_size: 64             # Chunks per embedding API call
    max_retries: 3             # Retries on transient failures
    rate_limit_tpm: 1000000    # Token-per-minute budget for rate limiting

  queue:
    backend: redis             # "redis" (local) or "sqs" (AWS)
    concurrency: 8             # Parallel embedding workers
    name: "cina:queue:ingestion"
    sqs_url_env: SQS_QUEUE_URL         # Env var name for SQS URL
    sqs_dlq_url_env: SQS_DLQ_URL       # Env var name for SQS DLQ URL
    sqs_region_env: AWS_REGION          # Env var name for AWS region
    sqs_endpoint_url_env: AWS_SQS_ENDPOINT_URL  # Env var for custom SQS endpoint

  sources:
    pubmed:
      enabled: true
      data_dir: /data/pubmed
    fda:
      enabled: true
      data_dir: /data/fda
    clinicaltrials:
      enabled: true
      api_base: https://clinicaltrials.gov/api/v2

serving:
  search:
    vector_top_k: 50           # Candidates from pgvector HNSW search
    bm25_top_k: 50             # Candidates from PostgreSQL FTS
    rrf_k: 60                  # RRF smoothing constant
    ef_search: 100             # HNSW ef_search runtime parameter

  rerank:
    model: cross-encoder/ms-marco-MiniLM-L-6-v2
    candidates: 20             # Top RRF results to rerank
    top_n: 10                  # Results to keep after reranking
    device: auto               # "auto", "cuda", or "cpu"

  context:
    max_chunks: 15             # Maximum chunks in assembled context
    generation_buffer_tokens: 2048  # Tokens reserved for LLM output

  stream:
    keepalive_interval_seconds: 15  # SSE keepalive comment interval

orchestration:
  providers:
    primary:
      name: anthropic
      model: claude-sonnet-4-20250514
      api_key_env: ANTHROPIC_API_KEY   # Env var containing the API key
      timeout_connect: 5.0     # Connection timeout (seconds)
      timeout_read: 60.0       # Read timeout (seconds)
    fallback:
      name: openai
      model: gpt-4o
      api_key_env: OPENAI_API_KEY
      timeout_connect: 5.0
      timeout_read: 60.0

  fallback:
    ttft_threshold_seconds: 5.0      # Start fallback race if TTFT exceeds this
    circuit_breaker_failures: 3      # Consecutive failures before circuit opens
    circuit_breaker_cooldown: 60     # Seconds before half-open retry

  cache:
    enabled: true
    num_hyperplanes: 16        # LSH hyperplanes for semantic hashing
    similarity_threshold: 0.95 # Cosine threshold for cache hit acceptance
    ttl_seconds: 86400         # Cache entry TTL (24 hours)

  rate_limit:
    requests_per_minute: 100
    tokens_per_hour: 100000

  prompt:
    default_version: "v1.0"    # Active prompt version

database:
  postgres:
    dsn_env: DATABASE_URL      # Env var containing PostgreSQL DSN
    pool_min: 5                # Minimum connection pool size
    pool_max: 20               # Maximum connection pool size
  redis:
    url_env: REDIS_URL         # Env var containing Redis URL
    pool_max: 20               # Maximum Redis connection pool size

observability:
  log_level: INFO              # DEBUG, INFO, WARNING, ERROR
  log_format: json             # "json" (structured) or "console" (human-readable)
  metrics_port: 9090           # Prometheus metrics port (if standalone)
  prometheus_path: /metrics    # Metrics endpoint path
```

---

## Environment Variable Reference

All settings can be overridden via environment variables using the `CINA__` prefix and `__` as the nesting delimiter.

**Pattern:** `CINA__<section>__<subsection>__<key>=<value>`

### Examples

| YAML Path | Environment Variable | Example Value |
|-----------|---------------------|---------------|
| `ingestion.chunk.max_tokens` | `CINA__INGESTION__CHUNK__MAX_TOKENS` | `512` |
| `ingestion.embedding.model` | `CINA__INGESTION__EMBEDDING__MODEL` | `text-embedding-3-large` |
| `ingestion.queue.backend` | `CINA__INGESTION__QUEUE__BACKEND` | `sqs` |
| `serving.search.vector_top_k` | `CINA__SERVING__SEARCH__VECTOR_TOP_K` | `50` |
| `serving.rerank.device` | `CINA__SERVING__RERANK__DEVICE` | `cuda` |
| `orchestration.fallback.ttft_threshold_seconds` | `CINA__ORCHESTRATION__FALLBACK__TTFT_THRESHOLD_SECONDS` | `5.0` |
| `orchestration.cache.enabled` | `CINA__ORCHESTRATION__CACHE__ENABLED` | `false` |
| `database.postgres.pool_max` | `CINA__DATABASE__POSTGRES__POOL_MAX` | `20` |
| `observability.log_level` | `CINA__OBSERVABILITY__LOG_LEVEL` | `DEBUG` |

### Sensitive Values (via env vars only)

These are referenced by name in `cina.yaml` but their actual values must be set as environment variables:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `OPENAI_API_KEY` | OpenAI API key (embeddings + fallback LLM) |
| `ANTHROPIC_API_KEY` | Anthropic API key (primary LLM) |
| `SQS_QUEUE_URL` | AWS SQS queue URL (production only) |
| `SQS_DLQ_URL` | AWS SQS dead-letter queue URL (production only) |
| `AWS_REGION` | AWS region (production only) |
| `CINA_AUTH_DISABLED` | Set to `1` to disable API key authentication |

---

## Docker Compose Environment

The `docker-compose.yml` pre-configures PostgreSQL and Redis with these connection strings:

```bash
DATABASE_URL=postgresql://cina:cina_dev@localhost:5432/cina
REDIS_URL=redis://localhost:6379/0
```

Prometheus is available at `http://localhost:9090`, Grafana at `http://localhost:3000` (admin/admin).

---

## Configuration Loading

Configuration is loaded by `cina/config/loader.py`:

1. Read `cina.yaml` from the project root (if it exists)
2. Parse into `FileConfig` (Pydantic model with `extra="ignore"`)
3. Merge with `AppConfig` (Pydantic Settings), which reads `CINA__*` env vars
4. Environment variables override YAML values at any nesting level
5. Final `AppConfig` instance is injected into all pipeline components
