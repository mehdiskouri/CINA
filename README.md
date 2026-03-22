# CINA — Clinical Index & Narrative Assembly

**A production-grade RAG backend for clinical literature search and synthesis.**

CINA ingests clinical documents from three authoritative sources — **PubMed Central**, **FDA DailyMed**, and **ClinicalTrials.gov** — and makes them searchable through a streaming API that returns cited, LLM-generated answers in real time.

The system combines **hybrid search** (dense vector retrieval + BM25 full-text search fused with Reciprocal Rank Fusion), **cross-encoder reranking**, **semantic caching**, and **multi-provider LLM orchestration** with circuit-breaker fallback to deliver accurate clinical answers under strict latency constraints. The full pipeline — from query embedding to context assembly — completes in under 90 ms before LLM generation begins.

### Highlights

- **98,602 chunks** from 3,500 clinical documents, embedded at 512 dimensions (OpenAI `text-embedding-3-large`)
- **Hybrid search in 7.5 ms** — pgvector HNSW + PostgreSQL GIN/BM25, fused with RRF
- **Cross-encoder reranking in 74.4 ms** — `ms-marco-MiniLM-L-6-v2` on CUDA
- **Semantic cache** via Redis LSH — sub-millisecond cache hits, saving ~$0.056 per repeated query
- **Circuit breaker + TTFT race** — automatic provider fallback (Anthropic → OpenAI) with shared state across replicas
- **SSE streaming** — metadata, tokens, citations, and per-query cost metrics in a single event stream
- **Infrastructure-as-code** — 7 Terraform modules for full AWS deployment (ECS Fargate, RDS, ElastiCache, SQS)

---

## Architecture

### System Overview

```mermaid
graph TB
    subgraph Sources["Data Sources"]
        PM[PubMed Central]
        FDA[FDA DailyMed]
        CT[ClinicalTrials.gov]
    end

    subgraph CINA["CINA Backend"]
        CLI[CLI / Admin]
        IP[Ingestion Pipeline]
        Q[Queue<br/>Redis Streams · SQS]
        EW[Embedding Worker<br/>OpenAI text-embedding-3-large]
        DB[(PostgreSQL + pgvector<br/>98,602 chunks · 512d HNSW)]
        RD[(Redis<br/>Cache · Rate Limit · Circuit Breaker)]
        API[FastAPI API Server]
        SP[Serving Pipeline]
    end

    subgraph Providers["LLM Providers"]
        AN[Anthropic Claude Sonnet 4<br/>Primary]
        OA[OpenAI GPT-4o<br/>Fallback]
    end

    subgraph Clients["API Clients"]
        HC[HTTP Client]
    end

    PM & FDA & CT -->|raw docs| IP
    CLI -->|trigger| IP
    IP -->|enqueue| Q
    Q -->|dequeue| EW
    EW -->|embed + store| DB
    HC -->|POST /v1/query| API
    API --> SP
    SP -->|hybrid search| DB
    SP -->|cache / rate limit| RD
    SP -->|LLM stream| AN
    SP -.->|fallback| OA
    SP -->|SSE response| HC
```

### Query Pipeline (Serving Path)

```mermaid
flowchart LR
    A[User Query] --> B[Embed Query<br/>OpenAI 512d]
    B --> C{Semantic Cache<br/>LSH Lookup}
    C -->|HIT| J[Stream Cached Response]
    C -->|MISS| D[Hybrid Search]
    D --> D1[Vector Search<br/>HNSW top-50]
    D --> D2[BM25 FTS<br/>GIN top-50]
    D1 & D2 --> E[Reciprocal Rank<br/>Fusion k=60]
    E --> F[Cross-Encoder Rerank<br/>MiniLM top-10]
    F --> G[Context Assembly<br/>Token Budget]
    G --> H[LLM Generation<br/>+ TTFT Race Fallback]
    H --> I[SSE Stream<br/>metadata · tokens · citations · metrics]
```

### Ingestion Pipeline

```mermaid
flowchart LR
    A[Source Files<br/>JSON / XML] --> B[Connector<br/>Parse → Document]
    B --> C[Section Extraction]
    C --> D[Chunking Engine<br/>512 tokens · sentence-aligned]
    D --> E[Queue<br/>Redis Streams / SQS]
    E --> F[Embedding Worker<br/>batch=64 · concurrency=8]
    F --> G[PostgreSQL + pgvector<br/>HNSW index]
```

### AWS Production Topology

```mermaid
graph TB
    subgraph VPC["VPC"]
        subgraph Public["Public Subnets"]
            ALB[Application Load Balancer<br/>HTTP :80]
        end
        subgraph Private["Private Subnets"]
            ECS1[ECS Fargate<br/>Query Service ×2]
            ECS2[ECS Fargate<br/>Ingestion Worker ×1]
            RDS[(RDS PostgreSQL<br/>db.t3.micro + pgvector)]
            EC[(ElastiCache Redis<br/>cache.t3.micro)]
            SQS[SQS + DLQ]
            S3[S3 Documents]
        end
    end

    Internet -->|HTTPS| ALB
    ALB --> ECS1
    ECS1 & ECS2 --> RDS
    ECS1 & ECS2 --> EC
    ECS2 --> SQS
    ECS2 --> S3
```

### Middleware Composition (Orchestration Layer)

```mermaid
flowchart LR
    A[Incoming Request] --> B[Rate Limiter]
    B --> C[Semantic Cache]
    C --> D[Provider Fallback<br/>Circuit Breaker + TTFT Race]
    D --> E[Cost Tracker]
    E --> F[LLM Provider<br/>Anthropic / OpenAI]
```

### Data Model

```mermaid
erDiagram
    Document ||--o{ Section : contains
    Section ||--o{ Chunk : contains
    Document ||--o{ Chunk : "denormalized FK"
    IngestionJob ||--o{ Document : produces
    QueryLog ||--o{ CostEvent : incurs

    Document {
        UUID id PK
        source_type source
        text source_id
        text title
        jsonb authors
        date publication_date
        UUID ingestion_id FK
    }
    Section {
        UUID id PK
        UUID document_id FK
        text section_type
        text heading
        int order
    }
    Chunk {
        UUID id PK
        UUID section_id FK
        UUID document_id FK
        text content
        int token_count
        vector_512 embedding
        text embedding_model
    }
    QueryLog {
        UUID id PK
        text query_text
        text provider_used
        bool cache_hit
        int total_latency_ms
    }
    CostEvent {
        UUID id PK
        UUID query_id FK
        text provider
        int input_tokens
        real estimated_cost_usd
    }
```

---

## Tech Stack

| Layer | Technology | Justification |
|-------|-----------|---------------|
| Language | Python 3.13 | Async-first, rich ML/NLP ecosystem |
| API Framework | FastAPI | Native async, automatic OpenAPI, SSE streaming |
| Embedding | OpenAI text-embedding-3-large (512d) | High-quality dense representations, dimension control |
| Primary LLM | Anthropic Claude Sonnet 4 | Best-in-class instruction following for clinical synthesis |
| Fallback LLM | OpenAI GPT-4o | Diversity of provider for resilience |
| Vector Store | PostgreSQL 16 + pgvector | HNSW index, co-located with relational data |
| Full-Text Search | PostgreSQL GIN / tsvector | BM25-equivalent without a separate search engine |
| Reranker | cross-encoder/ms-marco-MiniLM-L-6-v2 | Sub-100ms reranking on GPU, strong MSMARCO scores |
| Cache | Redis 7 | Semantic cache (LSH), rate limiting, circuit breaker state |
| Queue (local) | Redis Streams | Development parity without external dependencies |
| Queue (prod) | AWS SQS + DLQ | Managed, durable, with dead-letter redrive |
| IaC | Terraform (7 modules) | Repeatable, auditable AWS provisioning |
| Containers | Docker / ECS Fargate | Serverless container orchestration |
| Observability | Prometheus + Grafana | 17 custom metrics, pre-built dashboards |
| Config | Pydantic Settings + YAML | Type-safe, env-overridable, layered configuration |
| CLI | Typer | Subcommand groups for ingest, serve, db, apikey, dlq |

---

## Data Sources

| Source | Documents | Sections | Chunks | Duration | Notes |
|--------|----------|----------|--------|----------|-------|
| ClinicalTrials.gov | 1,000 | — | 8,844 | 144s | JSON from CT API v2 |
| PubMed Central | 2,000 | — | 71,316 | ~21 min | XML (JATS format) |
| FDA DailyMed | 499 | — | 18,442 | ~6 min | XML (SPL format), 1 retry on deadlock |
| **Total** | **3,500** | **68,005** | **98,602** | **~29 min** | 316 MB database dump |

---

## Quickstart

### Prerequisites

- Docker & Docker Compose
- Python 3.13+
- API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`

### 1. Start Infrastructure

```bash
docker compose up -d   # PostgreSQL 16, Redis 7, Prometheus, Grafana
```

### 2. Install & Configure

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# API keys
cp .env.example env.keys.local
# Edit env.keys.local with your keys
source env.keys.local

export DATABASE_URL="postgresql://cina:cina_dev@localhost:5432/cina"
export REDIS_URL="redis://localhost:6379/0"
```

### 3. Initialize Database

```bash
python -m cina db migrate
```

**Optional** — restore pre-embedded data (316 MB, 98,602 chunks):

```bash
gunzip -c cina_db_dump.sql.gz | pg_restore -U cina -d cina -h localhost --no-owner --clean 2>/dev/null
```

### 4. Ingest Documents

```bash
python -m cina ingest run --source clinicaltrials --path data/clinicaltrials
python -m cina ingest run --source pubmed --path data/pubmed
python -m cina ingest run --source fda --path data/fda
```

### 5. Create an API Key

```bash
python -m cina apikey create --name dev-key
# Output: cina_sk_<random>  (save this)
```

### 6. Start the Server

```bash
python -m cina serve --port 8000
```

### 7. Query

```bash
curl -N -X POST http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer cina_sk_<your_key>" \
  -d '{"query": "What are the latest treatments for metastatic breast cancer?"}'
```

Expected SSE stream:

```
event: metadata
data: {"query_id":"...","model":"claude-sonnet-4-20250514","provider":"anthropic","sources_used":10,"cache_hit":false}

event: token
data: {"text":"Based on the clinical literature..."}

event: citations
data: {"citations":[{"index":1,"document_title":"...","source":"pubmed",...}]}

event: metrics
data: {"search_latency_ms":7.5,"rerank_latency_ms":74.4,"llm_ttft_ms":1299.1,"estimated_cost_usd":0.056}

event: done
data: {}
```

> **Tip:** For local development without API key auth, set `CINA_AUTH_DISABLED=1`.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/query` | Submit a clinical question; returns SSE stream |
| `GET` | `/health` | Liveness check (Postgres connectivity) |
| `GET` | `/ready` | Readiness check (all dependencies) |
| `GET` | `/metrics` | Prometheus metrics (17 custom counters/histograms) |

Full API reference: [docs/api.md](docs/api.md)

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `cina ingest run --source <src> --path <dir>` | Ingest documents from a source |
| `cina serve --port 8000` | Start the FastAPI query server |
| `cina db migrate` | Run database migrations |
| `cina db reset` | Drop and recreate schema |
| `cina apikey create --name <n>` | Generate a new API key |
| `cina apikey revoke --key <k>` | Revoke an API key |
| `cina dlq list` | List dead-letter queue messages |
| `cina dlq retry --id <id>` | Retry a DLQ message |
| `cina dlq purge` | Purge all DLQ messages |

---

## Configuration

CINA uses layered configuration: `cina.yaml` → environment variables (`CINA__` prefix with `__` nesting).

Full configuration reference: [docs/configuration.md](docs/configuration.md)

---

## Performance Benchmarks

Measured on a single query ("What are the latest treatments for metastatic breast cancer?"):

| Stage | Latency | Notes |
|-------|---------|-------|
| Hybrid search (vector + BM25 + RRF) | **7.5 ms** | HNSW ef_search=100, GIN FTS |
| Cross-encoder rerank | **74.4 ms** | 20 → 10 candidates, CUDA |
| Context assembly | **4.9 ms** | Token-budgeted, skip-and-try |
| LLM time-to-first-token | **1,299 ms** | Claude Sonnet 4 |
| LLM total generation | **15,018 ms** | 2,794 output tokens |
| **Total (excl. LLM)** | **86.8 ms** | Well under 500 ms p95 target |
| Estimated cost per query | **$0.056** | 4,742 input + 2,794 output tokens |

Full benchmark data: [docs/benchmarks/README.md](docs/benchmarks/README.md) · Detailed pipeline report: [docs/PIPELINE_RUN_REPORT.md](docs/PIPELINE_RUN_REPORT.md) · Design decisions: [ADR.md](ADR.md)

---

## Design Decisions

Six architecture decision records document every significant design choice with full context, rejected alternatives, weighted decision matrices, and implementation evidence. The consolidated ADR document lives at **[ADR.md](ADR.md)**.

| # | Decision | Selected Option | Score | Runner-Up | Δ | Key Discriminator |
|---|----------|----------------|-------|-----------|---|-------------------|
| [001](ADR.md#adr-001-queue-abstraction-for-ingestion-workers) | Queue abstraction | `QueueProtocol` (Redis / SQS) | **9.00** | Amazon MQ (6.80) | +2.20 | Local dev simplicity + production DLQ parity |
| [002](ADR.md#adr-002-hybrid-search-with-reciprocal-rank-fusion) | Hybrid search | Vector + BM25 + RRF | **9.25** | Vector-only (7.75) | +1.50 | Lexical precision on clinical terminology |
| [003](ADR.md#adr-003-cross-encoder-reranking) | Reranking | cross-encoder MiniLM-L-6 | **8.50** | RRF-only (8.50) | 0.00 | Relevance quality tiebreaker (30% weight) |
| [004](ADR.md#adr-004-semantic-cache-via-redis-lsh) | Semantic cache | Redis LSH | **8.70** | pgvector cache (7.85) | +0.85 | Sub-ms lookup without database round-trip |
| [005](ADR.md#adr-005-provider-fallback-with-circuit-breaker-and-ttft-race) | Provider fallback | Circuit breaker + TTFT race | **8.55** | Round-robin (6.75) | +1.80 | Availability under degradation + tail latency |
| [006](ADR.md#adr-006-structure-aware-chunking) | Chunking strategy | Structure-aware, sentence-aligned | **8.85** | Recursive splitting (7.00) | +1.85 | Citation quality + section metadata |

Scores use a 1–10 Likert scale weighted by CINA's priorities (clinical correctness, latency, infrastructure simplicity). Full criteria weights, per-option breakdowns, and rejected-alternative rationale are in [ADR.md](ADR.md).

---

## Pipeline Run Report

A complete end-to-end pipeline run is documented in **[docs/PIPELINE_RUN_REPORT.md](docs/PIPELINE_RUN_REPORT.md)** with ingestion statistics, query latency breakdowns, SSE event traces, and degradation behaviour.

| Phase | Key Result | Detail |
|-------|-----------|--------|
| Ingestion | 3,500 docs → 98,602 chunks in ~29 min | 3 sources (PubMed, FDA, ClinicalTrials.gov), 68,005 sections, 1 deadlock retry |
| Hybrid search | 7.5 ms | Vector (HNSW top-50) + BM25 (GIN top-50) + RRF fusion, parallel via `asyncio.gather` |
| Cross-encoder rerank | 74.4 ms | 20 → 10 candidates, CUDA, ms-marco-MiniLM-L-6-v2 |
| Context assembly | 4.9 ms | Token-budgeted, skip-and-try, max 15 chunks |
| LLM TTFT | 1,299 ms | Claude Sonnet 4, 4,742 input tokens |
| LLM total generation | 15,018 ms | 2,794 output tokens, 10 cited sources |
| **Total pre-LLM** | **86.8 ms** | Well under 500 ms p95 target |
| Cost per query | $0.056 | Embedding + search + rerank + LLM generation |

---

## Observability

Pre-built Grafana dashboards are available at `http://localhost:3000` (admin/admin) when running via Docker Compose.

**17 Prometheus metrics** across three domains:

| Domain | Metrics |
|--------|---------|
| Ingestion | documents_processed, chunks_created, embedding_latency, queue_depth, dlq_messages, embedding_errors |
| Query Serving | query_total, query_latency, search_latency, rerank_latency, context_assembly_latency |
| Orchestration | provider_requests, provider_errors, provider_latency, cache_hits, cache_misses, rate_limit_rejected |

Dashboard screenshots: [docs/screenshots/](docs/screenshots/)

---

## AWS Deployment

CINA includes a complete Terraform configuration (7 modules, 26 files) for deploying to AWS:

```bash
# Build and push container images
./scripts/ecr_push.sh

# Deploy infrastructure
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # edit with your values
terraform init && terraform plan -out=tfplan && terraform apply tfplan

# Tear down
terraform destroy
```

Deployment verified with clean apply/destroy lifecycle. See:
- [Deployment Runbook](docs/terraform/DEPLOYMENT_RUNBOOK.md)
- [Cost Notes](docs/terraform/cost.md)
- [Demo Evidence](docs/demo/)

---

## Development

```bash
make lint          # ruff check
make format        # ruff format
make typecheck     # mypy strict
make test          # unit tests
make test-integration  # integration tests
```

---

## Known Limitations

See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for a full assessment.

---

## Project Structure

```
cina/
├── api/              # FastAPI app, routes, middleware, schemas
├── cli/              # Typer CLI (ingest, serve, db, apikey, dlq)
├── config/           # Pydantic Settings + YAML loader
├── db/               # asyncpg connection pool, migrations, repositories
├── ingestion/        # Connectors, chunking, embedding, queue
├── models/           # Domain models (Document, Query, Search, Cache)
├── observability/    # Structured logging (structlog) + Prometheus metrics
├── orchestration/    # Middleware composition, providers, cache, rate limiting
└── serving/          # Search, rerank, context assembly, LLM streaming
ADR.md                # 6 Architecture Decision Records (consolidated)
docs/
├── benchmarks/       # Benchmark summary and data
├── demo/             # Deployment evidence (terraform, smoke tests)
├── screenshots/      # Grafana dashboard screenshots
└── terraform/        # Deployment runbook and cost notes
infra/terraform/      # 7 Terraform modules (VPC, RDS, ElastiCache, SQS, S3, IAM, ECS)
scripts/              # Benchmarks, data acquisition, ECR push
tests/
├── unit/             # Unit tests (chunking, RRF, budget, LSH, rate limiter)
└── integration/      # Integration tests (ingestion pipeline, query path)
```

---

## Author

**Mehdi Skouri** — [GitHub](https://github.com/mehdiskouri)

## License

This project is licensed under the [MIT License](LICENSE).
