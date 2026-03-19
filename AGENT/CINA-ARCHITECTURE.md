# CINA — Architecture Document

## Clinical Index & Narrative Assembly

**Version:** 1.0
**Author:** Mehdi
**Date:** March 2026
**Status:** Pre-Implementation
**Companion:** [CINA-PRD.md](./CINA-PRD.md)

---

## Table of Contents

1. [System Context](#1-system-context)
2. [Design Principles](#2-design-principles)
3. [Layer Architecture](#3-layer-architecture)
4. [Data Model](#4-data-model)
5. [Ingestion Pipeline — Detailed Design](#5-ingestion-pipeline--detailed-design)
6. [Query Serving Layer — Detailed Design](#6-query-serving-layer--detailed-design)
7. [LLM Orchestration Layer — Detailed Design](#7-llm-orchestration-layer--detailed-design)
8. [Observability Architecture](#8-observability-architecture)
9. [Configuration Management](#9-configuration-management)
10. [Error Handling & Resilience](#10-error-handling--resilience)
11. [Security Considerations](#11-security-considerations)
12. [Local Development Architecture](#12-local-development-architecture)
13. [Production Architecture (AWS)](#13-production-architecture-aws)
14. [Package & Module Structure](#14-package--module-structure)
15. [Interface Contracts](#15-interface-contracts)
16. [Performance Budget](#16-performance-budget)
17. [Testing Architecture](#17-testing-architecture)
18. [Dependency Matrix](#18-dependency-matrix)

---

## 1. System Context

CINA operates as a headless backend service. It has no frontend, no user-facing UI, and no interactive chat interface. External actors interact with CINA through two boundaries:

```
                          ┌──────────────────┐
                          │   Data Sources    │
                          │  PubMed Central   │
                          │  FDA DailyMed     │
                          │  ClinicalTrials   │
                          └────────┬─────────┘
                                   │ raw documents
                                   ▼
┌──────────────┐          ┌──────────────────┐          ┌──────────────┐
│  CLI / Admin │─────────▶│      CINA        │◀─────────│  API Client  │
│  (ingestion) │  trigger │                  │  query   │  (any HTTP)  │
└──────────────┘          │  ┌────────────┐  │          └──────────────┘
                          │  │ PostgreSQL │  │
                          │  │ + pgvector │  │          ┌──────────────┐
                          │  └────────────┘  │─────────▶│ LLM Provider │
                          │  ┌────────────┐  │          │ Anthropic    │
                          │  │   Redis    │  │          │ OpenAI       │
                          │  └────────────┘  │          └──────────────┘
                          │  ┌────────────┐  │
                          │  │   Queue    │  │          ┌──────────────┐
                          │  │Redis/SQS   │  │─────────▶│ AWS Bedrock  │
                          │  └────────────┘  │          │ Titan Embed  │
                          └──────────────────┘          └──────────────┘
```

**Ingestion boundary:** An operator (or CI job) triggers ingestion via CLI. CINA fetches or reads source documents, processes them through the ingestion pipeline, and populates the database. This is a batch operation — no real-time coupling to the serving path.

**Query boundary:** An API client sends a clinical question via HTTP POST. CINA searches the index, re-ranks results, assembles context, orchestrates an LLM call, and streams a cited response via SSE. This is a real-time, latency-sensitive path.

**Provider boundary:** CINA calls external LLM APIs (OpenAI, Anthropic) for inference and Amazon Bedrock for embedding generation. These are outbound HTTP/SDK calls mediated by the orchestration and ingestion layers respectively.

---

## 2. Design Principles

These principles govern every architectural decision in CINA. When trade-offs arise, they are resolved in this priority order:

**P1 — Interface-driven composition.** Every major component is defined by a Python `Protocol`. Implementations are injected, not imported. This enables independent testing, local/production environment swapping, and future extension without modifying consuming code.

**P2 — Independent deployability and testability.** Each layer (ingestion, serving, orchestration) can be started, tested, and operated independently. The ingestion pipeline runs without the serving layer. The serving layer works against a pre-populated index without the ingestion pipeline. The orchestration components are composable middleware tested in isolation.

**P3 — Async-first, sync-never.** All I/O operations use `async`/`await`. No blocking calls in the serving path. Database access via `asyncpg`. HTTP calls via `httpx`. Queue operations via async interfaces. The only synchronous code is the cross-encoder inference (CPU/GPU-bound), which runs in a thread pool executor to avoid blocking the event loop.

**P4 — Observable by default.** Every operation emits structured logs and Prometheus metrics. A correlation ID (`query_id` or `ingestion_job_id`) propagates through all stages. No component is opaque — if something is slow or failing, the metrics and logs make it visible without code changes.

**P5 — Explicit over magical.** No framework abstractions that hide control flow. No LangChain chains, no LlamaIndex pipelines, no decorator-based orchestration. Every step in both the ingestion and query paths is a function call with typed inputs and outputs. The code reads top-to-bottom.

---

## 3. Layer Architecture

### 3.1 Layer Separation

```
┌─────────────────────────────────────────────────────────────────────┐
│                        API LAYER (FastAPI)                          │
│  Routes, request validation, SSE streaming, error responses         │
├─────────────────────────────────────────────────────────────────────┤
│                     ORCHESTRATION LAYER                              │
│  Provider routing │ Fallback │ Cache │ Rate limit │ Prompt version  │
├─────────────────────────────────────────────────────────────────────┤
│                      SERVING LAYER                                  │
│  Hybrid search │ RRF │ Cross-encoder │ Context assembly │ Citations │
├─────────────────────────────────────────────────────────────────────┤
│                     INGESTION LAYER                                 │
│  Connectors │ Parsing │ Chunking │ Embedding │ Queue workers        │
├─────────────────────────────────────────────────────────────────────┤
│                     INFRASTRUCTURE LAYER                            │
│  PostgreSQL/pgvector │ Redis │ Queue (Redis Streams/SQS) │ S3/FS   │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Dependency Direction

Dependencies flow downward. Upper layers depend on lower layers via protocols. Lower layers never import from upper layers. The infrastructure layer is accessed exclusively through repository and client abstractions — no raw SQL or Redis commands in business logic.

```
API Layer ──▶ Orchestration Layer ──▶ Serving Layer
                                          │
Ingestion Layer ─────────────────────────▶│
                                          ▼
                               Infrastructure Layer
```

The API layer depends on both the orchestration and serving layers. The ingestion layer depends only on the infrastructure layer. Serving and ingestion share the database but have no runtime coupling.

### 3.3 Concurrency Model

CINA uses a single-process, multi-coroutine model for the serving path. FastAPI's ASGI server (uvicorn) runs the event loop. All I/O-bound operations (database queries, HTTP calls to LLM providers, Redis operations) are awaited. The cross-encoder inference is CPU/GPU-bound and is dispatched to a `ThreadPoolExecutor` via `asyncio.run_in_executor()` to prevent event loop blocking.

The ingestion pipeline runs as a separate process (CLI or worker service). It consumes from the queue and processes documents concurrently using `asyncio.gather()` with a configurable concurrency limit (default: 8 concurrent documents).

```
Serving Process (uvicorn)
├── Event Loop
│   ├── Request handler coroutines (concurrent, I/O-bound)
│   ├── Database queries (asyncpg pool, awaited)
│   ├── LLM API calls (httpx.AsyncClient, awaited)
│   ├── Redis operations (aioredis, awaited)
│   └── Cross-encoder inference (ThreadPoolExecutor, run_in_executor)
│
Ingestion Process (CLI / worker)
├── Event Loop
│   ├── Queue consumer coroutine
│   ├── Document processing coroutines (bounded concurrency)
│   ├── Embedding API calls (httpx.AsyncClient, batched, awaited)
│   └── Database writes (asyncpg pool, awaited)
```

---

## 4. Data Model

### 4.1 Entity Relationship Diagram

```
┌────────────────┐       ┌────────────────┐       ┌────────────────────┐
│    Document     │──1:N──│    Section      │──1:N──│       Chunk        │
├────────────────┤       ├────────────────┤       ├────────────────────┤
│ id (PK, UUID)  │       │ id (PK, UUID)  │       │ id (PK, UUID)      │
│ source         │       │ document_id(FK)│       │ section_id (FK)    │
│ source_id      │       │ section_type   │       │ document_id (FK)   │
│ title          │       │ heading        │       │ content            │
│ authors (JSON) │       │ content        │       │ content_hash       │
│ publication_dt │       │ order          │       │ token_count        │
│ raw_metadata   │       │ created_at     │       │ chunk_index        │
│ ingestion_id   │       └────────────────┘       │ overlap_tokens     │
│ created_at     │                                │ embedding (vector) │
│ updated_at     │                                │ embedding_model    │
└────────────────┘                                │ embedding_dim      │
                                                  │ metadata (JSON)    │
       ┌────────────────┐                         │ created_at         │
       │ IngestionJob   │                         └────────────────────┘
       ├────────────────┤
       │ id (PK, UUID)  │       ┌────────────────────┐
       │ source         │       │   PromptVersion     │
       │ status         │       ├────────────────────┤
       │ documents_total│       │ id (PK, str)       │
       │ documents_done │       │ system_prompt      │
       │ chunks_created │       │ description        │
       │ errors (JSON)  │       │ traffic_weight     │
       │ started_at     │       │ active             │
       │ completed_at   │       │ created_at         │
       └────────────────┘       └────────────────────┘

       ┌────────────────────┐   ┌────────────────────┐
       │    QueryLog        │   │     CostEvent      │
       ├────────────────────┤   ├────────────────────┤
       │ id (PK, UUID)     │   │ id (PK, UUID)      │
       │ query_text        │   │ query_id (FK)      │
       │ query_embedding   │   │ tenant_id          │
       │ prompt_version_id │   │ provider           │
       │ provider_used     │   │ model              │
       │ fallback_triggered│   │ input_tokens       │
       │ cache_hit         │   │ output_tokens      │
       │ total_latency_ms  │   │ estimated_cost_usd │
       │ search_latency_ms │   │ cache_hit          │
       │ rerank_latency_ms │   │ created_at         │
       │ llm_latency_ms    │   └────────────────────┘
       │ chunks_retrieved  │
       │ chunks_used       │
       │ tenant_id         │
       │ created_at        │
       └────────────────────┘
```

### 4.2 PostgreSQL Schema

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- trigram similarity (optional, for fuzzy matching)

-- Enum types
CREATE TYPE source_type AS ENUM ('pubmed', 'fda', 'clinicaltrials');
CREATE TYPE ingestion_status AS ENUM ('pending', 'running', 'completed', 'failed');

-- Documents table
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          source_type NOT NULL,
    source_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    authors         JSONB DEFAULT '[]',
    publication_date DATE,
    raw_metadata    JSONB DEFAULT '{}',
    ingestion_id    UUID NOT NULL REFERENCES ingestion_jobs(id),
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source, source_id)
);

-- Sections table
CREATE TABLE sections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_type    TEXT NOT NULL,
    heading         TEXT,
    content         TEXT NOT NULL,
    "order"         INTEGER NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_sections_document ON sections(document_id);

-- Chunks table (core retrieval entity)
CREATE TABLE chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_id      UUID NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    token_count     INTEGER NOT NULL,
    chunk_index     INTEGER NOT NULL,
    overlap_tokens  INTEGER DEFAULT 0,
    embedding       vector(512),
    embedding_model TEXT NOT NULL,
    embedding_dim   INTEGER NOT NULL DEFAULT 512,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (content_hash, embedding_model)
);

-- pgvector HNSW index for approximate nearest neighbor search
CREATE INDEX idx_chunks_embedding_hnsw ON chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

-- Full-text search index for BM25 path
ALTER TABLE chunks ADD COLUMN content_tsvector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX idx_chunks_fts ON chunks USING gin(content_tsvector);

-- Document-level full-text for broader searches
CREATE INDEX idx_documents_title_fts ON documents
    USING gin(to_tsvector('english', title));

-- Ingestion jobs table
CREATE TABLE ingestion_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          source_type NOT NULL,
    status          ingestion_status DEFAULT 'pending',
    documents_total INTEGER DEFAULT 0,
    documents_done  INTEGER DEFAULT 0,
    chunks_created  INTEGER DEFAULT 0,
    errors          JSONB DEFAULT '[]',
    config          JSONB DEFAULT '{}',
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Prompt versions table
CREATE TABLE prompt_versions (
    id              TEXT PRIMARY KEY,
    system_prompt   TEXT NOT NULL,
    description     TEXT,
    traffic_weight  REAL DEFAULT 0.0 CHECK (traffic_weight >= 0.0 AND traffic_weight <= 1.0),
    active          BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Query log table
CREATE TABLE query_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text          TEXT NOT NULL,
    query_embedding     vector(512),
    prompt_version_id   TEXT REFERENCES prompt_versions(id),
    provider_used       TEXT NOT NULL,
    fallback_triggered  BOOLEAN DEFAULT false,
    cache_hit           BOOLEAN DEFAULT false,
    total_latency_ms    INTEGER,
    search_latency_ms   INTEGER,
    rerank_latency_ms   INTEGER,
    llm_latency_ms      INTEGER,
    chunks_retrieved    INTEGER,
    chunks_used         INTEGER,
    tenant_id           TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_query_logs_created ON query_logs(created_at DESC);
CREATE INDEX idx_query_logs_tenant ON query_logs(tenant_id, created_at DESC);

-- Cost events table
CREATE TABLE cost_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_id            UUID REFERENCES query_logs(id),
    tenant_id           TEXT,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    estimated_cost_usd  REAL NOT NULL,
    cache_hit           BOOLEAN DEFAULT false,
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_cost_events_tenant ON cost_events(tenant_id, created_at DESC);
CREATE INDEX idx_cost_events_provider ON cost_events(provider, created_at DESC);
```

### 4.3 Redis Key Schema

All Redis keys are prefixed with `cina:` to namespace within shared instances.

```
Semantic Cache:
  cina:cache:lsh:{hash_hex}       → JSON {response, citations, prompt_version, embedding, created_at}
  TTL: 86400 (24 hours)

Rate Limiting:
  cina:ratelimit:{tenant_id}:req  → Sorted set (timestamps as scores, member = request_id)
  cina:ratelimit:{tenant_id}:tok  → Sorted set (timestamps as scores, member = token_count)
  TTL: auto-trimmed by sliding window

Provider Health:
  cina:provider:{name}:failures   → Integer (consecutive failure count)
  cina:provider:{name}:circuit    → String ("closed" | "open" | "half_open")
  cina:provider:{name}:cooldown   → Key with TTL (presence = cooldown active)

Queue (Redis Streams, local only):
  cina:queue:ingestion            → Redis Stream (pending documents)
  cina:queue:ingestion:dlq        → Redis Stream (dead-letter queue)
  Consumer group: cina-workers
```

---

## 5. Ingestion Pipeline — Detailed Design

### 5.1 Pipeline Flow

```
 Source Data
     │
     ▼
┌─────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Fetch / │───▶│  Parse &  │───▶│  Chunk   │───▶│  Embed   │───▶│  Store   │
│  Read   │    │ Normalize │    │          │    │ (batched)│    │ pgvector │
└─────────┘    └───────────┘    └──────────┘    └──────────┘    └──────────┘
     │              │                │                │               │
     ▼              ▼                ▼                ▼               ▼
  Raw file     Document obj     Chunk list     Chunk + embed    DB commit
  archived     + sections       + metadata     vectors          + audit log
  (S3/FS)
```

### 5.2 Source Connectors

Each connector implements the `SourceConnector` protocol:

```python
class SourceConnector(Protocol):
    source_type: source_type

    async def fetch_document_list(
        self, config: FetchConfig
    ) -> AsyncIterator[RawDocument]: ...

    def parse(self, raw: RawDocument) -> Document: ...
```

`RawDocument` is the unparsed payload (XML bytes, JSON string) with source metadata. `Document` is the normalized internal representation.

**PubMed Central connector:**

- Fetches article XML via PMC OAI-PMH service with resumption tokens for pagination, or reads from a local directory of pre-downloaded XMLs.
- Parses JATS XML using `lxml`. Extracts structured sections by `<sec>` elements, preserving `sec-type` attributes. Handles nested sections by flattening to a single level with composite headings (e.g., "Methods > Statistical Analysis").
- Extracts inline references and citation markers for potential future cross-referencing.
- Strips figure/table captions into separate sections with type `figure_caption` / `table_caption`.

**FDA DailyMed connector:**

- Fetches SPL XML via DailyMed REST API or reads from bulk download directory.
- Parses SPL XML using `lxml`. Sections map directly to drug label sections (Indications and Usage, Dosage and Administration, Contraindications, Warnings, Adverse Reactions, Drug Interactions, Clinical Pharmacology, etc.).
- Preserves structured dosage data and drug identification fields in `raw_metadata`.

**ClinicalTrials.gov connector:**

- Fetches study records via ClinicalTrials.gov API v2 (JSON responses) with cursor-based pagination.
- Maps JSON fields to sections: Brief Summary → abstract, Eligibility Criteria → eligibility, Intervention/Treatment → intervention, Primary/Secondary Outcomes → outcomes, Results → results.
- Preserves trial phase, status, sponsor, and condition tags in `raw_metadata`.

### 5.3 Chunking Engine

The chunking engine is stateless and deterministic. Given the same `Document` and `ChunkConfig`, it always produces the same chunks.

```python
@dataclass(frozen=True)
class ChunkConfig:
    max_chunk_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"  # tiktoken encoding matching the embedding model
    respect_section_boundaries: bool = True
    sentence_boundary_alignment: bool = True
```

**Pass 1 — Structure-aware splitting:**

```
for section in document.sections:
    tokens = tokenize(section.content)
    if len(tokens) <= max_chunk_tokens:
        yield Chunk(content=section.content, chunk_index=0, overlap_tokens=0)
    else:
        yield from sliding_window_chunk(section, config)
```

**Pass 2 — Sliding window for oversized sections:**

```
def sliding_window_chunk(section, config):
    sentences = split_sentences(section.content)
    current_chunk_sentences = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = count_tokens(sentence)
        if current_tokens + sentence_tokens > config.max_chunk_tokens:
            yield build_chunk(current_chunk_sentences)
            # Overlap: retain trailing sentences up to overlap_tokens
            overlap_sentences, overlap_count = compute_overlap(
                current_chunk_sentences, config.overlap_tokens
            )
            current_chunk_sentences = overlap_sentences
            current_tokens = overlap_count
        current_chunk_sentences.append(sentence)
        current_tokens += sentence_tokens

    if current_chunk_sentences:
        yield build_chunk(current_chunk_sentences)
```

**Sentence boundary detection:** Uses a rule-based splitter aware of common medical abbreviations (e.g., "i.v.", "p.o.", "b.i.d.", "Dr.", "Fig.", "et al.") to avoid false splits. Not an ML model — deterministic and fast.

**Content hashing:** Each chunk's `content_hash` is `sha256(content + embedding_model)`. This enables idempotent re-ingestion — if a document is re-processed and a chunk's content hasn't changed, the existing embedding is reused.

### 5.4 Embedding Worker

The embedding worker consumes chunks from the queue, batches them, and calls Amazon Bedrock's Titan Embeddings V2 API.

```
Queue ──▶ Batch accumulator ──▶ Bedrock Titan V2 ──▶ Database write
              (64 chunks)         (single call)       (bulk upsert)
```

**Batching logic:**

```python
async def embedding_worker(queue: QueueProtocol, config: EmbedConfig):
    batch: list[Chunk] = []
    async for message in queue.consume("ingestion:embed"):
        chunk = deserialize(message)
        batch.append(chunk)
        if len(batch) >= config.batch_size:
            await process_batch(batch, config)
            batch = []
    if batch:  # flush remaining
        await process_batch(batch, config)

async def process_batch(batch: list[Chunk], config: EmbedConfig):
    texts = [chunk.content for chunk in batch]
    try:
        embeddings = await embedding_provider.embed(
            texts,
            model=config.model,           # "amazon.titan-embed-text-v2:0"
            dimensions=config.dimensions,  # 512
        )
        await chunk_repository.bulk_upsert_embeddings(batch, embeddings)
        for chunk in batch:
            await queue.acknowledge(chunk.receipt)
    except EmbeddingAPIError as e:
        for chunk in batch:
            if chunk.retry_count >= config.max_retries:
                await queue.dead_letter(chunk, reason=str(e))
            else:
                await queue.enqueue(chunk.with_retry_increment(), "ingestion:embed")
```

**Rate limiting:** The embedding client enforces a token-per-minute budget aligned with the Bedrock service quota. Uses a token bucket algorithm internally. Bedrock's default TPM limits are significantly higher than OpenAI's, and can be increased via AWS Service Quotas without manual approval for most tiers. Batches are dispatched at the maximum rate the service allows.

**Idempotency:** Before embedding, each chunk's `content_hash + embedding_model` is checked against the database. Already-embedded chunks are skipped. This makes re-ingestion safe and cheap.

### 5.5 Ingestion Job Lifecycle

```
PENDING ──▶ RUNNING ──▶ COMPLETED
                │
                └──▶ FAILED (with error details in JSON)
```

The `IngestionJob` record tracks progress. The CLI polls job status and displays a progress bar. If a job fails mid-way, re-running it with the same source and config resumes from where it left off (idempotent chunks, duplicate-safe upserts).

---

## 6. Query Serving Layer — Detailed Design

### 6.1 Query Processing Pipeline

```
POST /v1/query
      │
      ▼
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Embed   │───▶│  Hybrid  │───▶│ Re-rank  │───▶│ Context  │
│  Query   │    │  Search  │    │ (cross-  │    │ Assembly │
│          │    │(vec+BM25)│    │ encoder) │    │ + budget │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                      │
      ┌───────────────────────────────────────────────┘
      ▼
┌──────────┐    ┌──────────┐    ┌──────────┐
│  Cache   │───▶│   LLM    │───▶│  Stream  │
│  Check   │    │  Call     │    │  SSE     │
│ (LSH)    │    │(orchestr)│    │ Response │
└──────────┘    └──────────┘    └──────────┘
```

### 6.2 Query Embedding

The incoming query is embedded using the same model and dimensionality as the indexed chunks (Titan Embeddings V2, dim 512 via Bedrock). This is a single API call (not batched — latency-sensitive) via the embedding provider with a dedicated connection.

```python
async def embed_query(query: str, config: EmbedConfig) -> list[float]:
    response = await embedding_provider.embed(
        [query],
        model=config.model,
        dimensions=config.dimensions,
    )
    return response[0]
```

The query embedding is computed once and reused across three consumers: vector search, semantic cache lookup, and query logging.

### 6.3 Hybrid Search

Two parallel retrieval paths run concurrently via `asyncio.gather()`:

**Dense retrieval (pgvector):**

```sql
SELECT id, content, metadata, 1 - (embedding <=> $1) AS similarity
FROM chunks
WHERE embedding IS NOT NULL
ORDER BY embedding <=> $1
LIMIT $2;
-- $1 = query embedding vector, $2 = top_k (default 50)
```

The `<=>` operator uses cosine distance via the HNSW index. The `ef_search` parameter (default: 100) is set at session level to control recall/speed tradeoff.

**Sparse retrieval (BM25 via tsvector):**

```sql
SELECT id, content, metadata,
       ts_rank_cd(content_tsvector, plainto_tsquery('english', $1)) AS bm25_score
FROM chunks
WHERE content_tsvector @@ plainto_tsquery('english', $1)
ORDER BY bm25_score DESC
LIMIT $2;
-- $1 = raw query text, $2 = top_k (default 50)
```

`ts_rank_cd` uses cover density ranking, which considers the proximity of matching terms. The `plainto_tsquery` function handles natural language queries (no special syntax required from the user).

**Concurrent execution:**

```python
async def hybrid_search(query_text: str, query_embedding: list[float], config: SearchConfig):
    vector_results, bm25_results = await asyncio.gather(
        vector_search(query_embedding, config.vector_top_k),
        bm25_search(query_text, config.bm25_top_k),
    )
    fused = reciprocal_rank_fusion(vector_results, bm25_results, k=config.rrf_k)
    return fused[:config.rerank_candidates]
```

### 6.4 Reciprocal Rank Fusion

RRF merges two ranked lists without requiring score normalization:

```python
def reciprocal_rank_fusion(
    *result_lists: list[SearchResult],
    k: int = 60,
) -> list[SearchResult]:
    scores: dict[UUID, float] = defaultdict(float)
    result_map: dict[UUID, SearchResult] = {}

    for result_list in result_lists:
        for rank, result in enumerate(result_list):
            scores[result.chunk_id] += 1.0 / (k + rank + 1)
            result_map[result.chunk_id] = result

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [result_map[chunk_id] for chunk_id, _ in ranked]
```

The `k` parameter (default: 60) controls how much rank differences are smoothed. Higher `k` gives more weight to the top ranks of each list. This is a tunable parameter benchmarked during Phase 2.

### 6.5 Cross-Encoder Re-Ranking

The top-n RRF candidates (default: 20) are re-scored by the cross-encoder:

```python
class CrossEncoderReranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model = CrossEncoder(model_name, device=self._detect_device())
        self._executor = ThreadPoolExecutor(max_workers=1)

    async def rerank(
        self, query: str, candidates: list[SearchResult], top_n: int = 10
    ) -> list[SearchResult]:
        pairs = [(query, c.content) for c in candidates]
        # Run synchronous inference in thread pool to avoid blocking event loop
        scores = await asyncio.get_event_loop().run_in_executor(
            self._executor,
            lambda: self.model.predict(pairs),
        )
        scored = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [candidate for candidate, _ in scored[:top_n]]
```

**Latency budget:** 20 candidates × MiniLM-L-6 ≈ 50ms on GPU, ~500ms on CPU. The performance budget (Section 16) allocates 100ms for re-ranking at p95 on GPU.

**Thread pool isolation:** The cross-encoder runs in a single-thread executor. Only one re-ranking operation runs at a time per process. Under high concurrency, re-ranking requests queue in the executor. This is acceptable at portfolio demonstration scale. A production system would use a dedicated inference service — documented as a known scaling limitation, not a design flaw.

### 6.6 Context Assembly

After re-ranking, the top chunks are packed into the LLM context window:

```python
@dataclass
class ContextBudget:
    model_context_limit: int      # e.g., 128000 tokens
    system_prompt_tokens: int     # measured from the active prompt version
    query_tokens: int             # measured from the user query
    generation_buffer: int = 2048 # reserved for model output
    max_chunks: int = 15          # hard cap on source count

    @property
    def available(self) -> int:
        return (
            self.model_context_limit
            - self.system_prompt_tokens
            - self.query_tokens
            - self.generation_buffer
        )

def assemble_context(
    ranked_chunks: list[SearchResult],
    budget: ContextBudget,
) -> list[NumberedSource]:
    sources: list[NumberedSource] = []
    tokens_used = 0

    for i, chunk in enumerate(ranked_chunks):
        if len(sources) >= budget.max_chunks:
            break
        if tokens_used + chunk.token_count > budget.available:
            continue  # skip this chunk, try smaller ones
        sources.append(NumberedSource(
            index=len(sources) + 1,
            chunk=chunk,
        ))
        tokens_used += chunk.token_count

    return sources
```

The `continue` on budget overflow (rather than `break`) implements a greedy "skip and try" strategy. If a large chunk doesn't fit, a smaller subsequent chunk might. This maximizes information density within the budget.

### 6.7 Prompt Construction

The system prompt and context are assembled into the final messages array:

```python
def build_messages(
    query: str,
    sources: list[NumberedSource],
    prompt_version: PromptVersion,
) -> list[Message]:
    context_block = "\n\n".join(
        f"[Source {s.index}] ({s.chunk.metadata['source']}: {s.chunk.metadata['title']}, "
        f"Section: {s.chunk.metadata['section_type']})\n{s.chunk.content}"
        for s in sources
    )

    return [
        Message(role="system", content=prompt_version.system_prompt),
        Message(role="user", content=(
            f"Answer the following clinical question using ONLY the provided sources. "
            f"Cite sources using [1], [2], etc. If the sources do not contain sufficient "
            f"information, say so explicitly.\n\n"
            f"Sources:\n{context_block}\n\n"
            f"Question: {query}"
        )),
    ]
```

### 6.8 SSE Stream Protocol

The streaming response follows a structured event protocol:

```
event: metadata
data: {
    "query_id": "uuid",
    "model": "claude-sonnet-4-20250514",
    "provider": "anthropic",
    "prompt_version": "v1.2",
    "sources_used": 7,
    "cache_hit": false
}

event: token
data: {"text": "Metformin"}

event: token
data: {"text": " is generally"}

... (one event per token or token group)

event: citations
data: {
    "citations": [
        {
            "index": 1,
            "document_title": "Renal Safety of Metformin...",
            "source": "pubmed",
            "source_id": "PMC7234567",
            "section_type": "contraindications",
            "authors": ["Smith J", "Doe A"],
            "publication_date": "2023-06-15"
        },
        ...
    ]
}

event: metrics
data: {
    "search_latency_ms": 45,
    "rerank_latency_ms": 52,
    "assembly_latency_ms": 3,
    "llm_ttft_ms": 890,
    "llm_total_ms": 2340,
    "input_tokens": 4521,
    "output_tokens": 847,
    "estimated_cost_usd": 0.0023
}

event: done
data: {}
```

The `metadata` event fires immediately after search/rerank/assembly completes (before LLM generation starts). The client knows the query is being answered before any tokens arrive. Token events proxy the LLM's streaming output. `citations` and `metrics` fire after generation completes.

### 6.9 SSE Implementation

```python
@app.post("/v1/query")
async def query_endpoint(request: QueryRequest) -> StreamingResponse:
    query_id = uuid4()

    async def event_stream():
        # 1. Embed query
        query_embedding = await embed_query(request.query, embed_config)

        # 2. Check semantic cache
        cached = await semantic_cache.lookup(query_embedding)
        if cached:
            yield sse_event("metadata", {**cached.metadata, "cache_hit": True})
            for token in cached.tokens:
                yield sse_event("token", {"text": token})
            yield sse_event("citations", cached.citations)
            yield sse_event("metrics", cached.metrics)
            yield sse_event("done", {})
            return

        # 3. Hybrid search
        candidates = await hybrid_search(request.query, query_embedding, search_config)

        # 4. Re-rank
        ranked = await reranker.rerank(request.query, candidates)

        # 5. Assemble context
        sources = assemble_context(ranked, context_budget)

        # 6. Select prompt version and provider
        prompt_version = await prompt_router.select()
        provider = await provider_router.select()

        # 7. Emit metadata
        yield sse_event("metadata", {
            "query_id": str(query_id),
            "model": provider.model,
            "provider": provider.name,
            "prompt_version": prompt_version.id,
            "sources_used": len(sources),
            "cache_hit": False,
        })

        # 8. LLM streaming call
        messages = build_messages(request.query, sources, prompt_version)
        collected_tokens = []
        async for chunk in orchestrator.complete(messages, provider):
            collected_tokens.append(chunk.text)
            yield sse_event("token", {"text": chunk.text})

        # 9. Citations
        citation_data = [source.to_citation_dict() for source in sources]
        yield sse_event("citations", {"citations": citation_data})

        # 10. Metrics and logging (fire-and-forget)
        metrics = compute_metrics(...)
        yield sse_event("metrics", metrics)
        yield sse_event("done", {})

        # 11. Background: cache response, log query
        asyncio.create_task(semantic_cache.store(query_embedding, ...))
        asyncio.create_task(log_query(query_id, ...))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

---

## 7. LLM Orchestration Layer — Detailed Design

### 7.1 Component Composition

The orchestration layer is not a monolithic service. It is a pipeline of composable middleware components that wrap the LLM call:

```
                    ┌─────────────────────────────┐
                    │      Rate Limiter            │
                    │  Check tenant budget         │
                    │  Reject if exceeded          │
                    ├─────────────────────────────┤
                    │      Semantic Cache          │
                    │  LSH lookup                  │
                    │  Return cached if hit        │
                    ├─────────────────────────────┤
                    │      Provider Router         │
                    │  Select provider             │
                    │  Health check registry       │
                    ├─────────────────────────────┤
                    │      Fallback Handler        │
                    │  Circuit breaker             │
                    │  Concurrent timeout race     │
                    ├─────────────────────────────┤
                    │      LLM Provider            │
                    │  OpenAI / Anthropic          │
                    │  Streaming response          │
                    ├─────────────────────────────┤
                    │      Cost Tracker            │
                    │  Log tokens and cost         │
                    └─────────────────────────────┘
```

Each component implements a wrapper pattern:

```python
class Middleware(Protocol):
    async def __call__(
        self,
        messages: list[Message],
        config: CompletionConfig,
        next_handler: Callable,
    ) -> AsyncIterator[StreamChunk]: ...
```

The pipeline is assembled at startup:

```python
pipeline = compose(
    rate_limiter,
    semantic_cache,
    provider_router,
    fallback_handler,
    cost_tracker,
)
# Usage: async for chunk in pipeline(messages, config): ...
```

### 7.2 Provider Implementations

**OpenAI provider:**

```python
class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0),
        )
        self.model = model

    async def complete(self, messages, config) -> AsyncIterator[StreamChunk]:
        async with self.client.stream("POST", "/chat/completions", json={
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"]
                    if "content" in delta:
                        yield StreamChunk(text=delta["content"])
```

**Anthropic provider:**

```python
class AnthropicProvider:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.client = httpx.AsyncClient(
            base_url="https://api.anthropic.com/v1",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0),
        )
        self.model = model

    async def complete(self, messages, config) -> AsyncIterator[StreamChunk]:
        system = next((m.content for m in messages if m.role == "system"), None)
        user_messages = [m.to_dict() for m in messages if m.role != "system"]
        async with self.client.stream("POST", "/messages", json={
            "model": self.model,
            "system": system,
            "messages": user_messages,
            "stream": True,
            "max_tokens": config.max_tokens,
        }) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if event["type"] == "content_block_delta":
                        yield StreamChunk(text=event["delta"]["text"])
```

Both providers implement the same `LLMProviderProtocol`. The serving layer never imports provider-specific code.

### 7.3 Circuit Breaker

State machine for each provider:

```
        success                    timeout expires
  ┌──────────────┐          ┌─────────────────────┐
  │              ▼          ▼                      │
  │          ┌────────┐  ┌───────────┐  ┌────────┐│
  │          │ CLOSED │  │ HALF_OPEN │  │  OPEN  ││
  │          └───┬────┘  └─────┬─────┘  └───┬────┘│
  │              │             │             │     │
  │    N failures│    success  │    failure  │     │
  │              │      │      │      │      │     │
  │              ▼      │      ▼      │      ▼     │
  │          ┌────────┐ │  ┌───────┐  │  ┌────────┘
  │          │  OPEN  │─┘  │CLOSED │  └─▶│  OPEN  │
  │          └────────┘    └───────┘     └────────┘
  └────────────────────────────┘
```

```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds

    async def is_available(self, provider_name: str) -> bool:
        state = await self.redis.get(f"cina:provider:{provider_name}:circuit")
        if state == "open":
            cooldown = await self.redis.exists(f"cina:provider:{provider_name}:cooldown")
            if not cooldown:
                await self.redis.set(f"cina:provider:{provider_name}:circuit", "half_open")
                return True  # allow one probe request
            return False
        return True

    async def record_success(self, provider_name: str):
        await self.redis.set(f"cina:provider:{provider_name}:failures", 0)
        await self.redis.set(f"cina:provider:{provider_name}:circuit", "closed")

    async def record_failure(self, provider_name: str):
        failures = await self.redis.incr(f"cina:provider:{provider_name}:failures")
        if failures >= self.failure_threshold:
            await self.redis.set(f"cina:provider:{provider_name}:circuit", "open")
            await self.redis.setex(
                f"cina:provider:{provider_name}:cooldown",
                self.cooldown_seconds,
                "1",
            )
```

### 7.4 Concurrent Timeout Fallback

When the primary provider is slow (time-to-first-token exceeds threshold), CINA races a fallback request:

```python
async def complete_with_fallback(
    messages: list[Message],
    config: CompletionConfig,
    primary: LLMProvider,
    fallback: LLMProvider,
    ttft_threshold: float = 5.0,
) -> AsyncIterator[StreamChunk]:
    primary_started = asyncio.Event()
    first_token = asyncio.Event()

    async def primary_stream():
        async for chunk in primary.complete(messages, config):
            first_token.set()
            yield chunk

    async def timeout_monitor():
        try:
            await asyncio.wait_for(first_token.wait(), timeout=ttft_threshold)
        except asyncio.TimeoutError:
            return True  # signal: start fallback
        return False  # primary responded in time

    need_fallback = await timeout_monitor()

    if not need_fallback:
        async for chunk in primary_stream():
            yield chunk
    else:
        # Primary too slow — race fallback
        # Cancel primary, stream from fallback
        async for chunk in fallback.complete(messages, config):
            yield chunk
```

This is a simplified representation. The actual implementation handles edge cases: primary returning tokens after fallback starts (cancel primary), both failing (propagate error), and metrics recording for which provider won.

### 7.5 Semantic Cache (LSH)

**Locality-Sensitive Hashing:**

```python
class LSHCache:
    def __init__(
        self,
        num_hyperplanes: int = 16,
        embedding_dim: int = 512,
        similarity_threshold: float = 0.95,
        ttl_seconds: int = 86400,
    ):
        # Random hyperplanes generated once at startup, persisted to Redis
        self.hyperplanes = self._load_or_generate_hyperplanes(num_hyperplanes, embedding_dim)
        self.similarity_threshold = similarity_threshold
        self.ttl = ttl_seconds

    def _hash_embedding(self, embedding: list[float]) -> str:
        vec = np.array(embedding)
        bits = (self.hyperplanes @ vec > 0).astype(int)
        return bits.tobytes().hex()

    async def lookup(self, query_embedding: list[float]) -> CachedResponse | None:
        hash_key = self._hash_embedding(query_embedding)
        cached_json = await self.redis.get(f"cina:cache:lsh:{hash_key}")
        if cached_json is None:
            return None

        cached = json.loads(cached_json)
        # Verify cosine similarity exceeds threshold
        similarity = cosine_similarity(query_embedding, cached["embedding"])
        if similarity >= self.similarity_threshold:
            return CachedResponse(**cached)
        return None  # hash collision, not actually similar enough

    async def store(
        self,
        query_embedding: list[float],
        response: ResponseData,
    ):
        hash_key = self._hash_embedding(query_embedding)
        payload = json.dumps({
            "embedding": query_embedding,
            "tokens": response.tokens,
            "citations": response.citations,
            "metadata": response.metadata,
            "metrics": response.metrics,
            "prompt_version": response.prompt_version,
            "created_at": datetime.utcnow().isoformat(),
        })
        await self.redis.setex(f"cina:cache:lsh:{hash_key}", self.ttl, payload)
```

**Cache invalidation:** When a prompt version is deactivated, all cache entries tagged with that version are invalidated. This is implemented via a background scan with cursor iteration (not `KEYS *` — that blocks Redis). Prompt version changes are infrequent enough that this scan is acceptable.

### 7.6 Rate Limiter

Sliding window rate limiter using Redis sorted sets:

```python
class SlidingWindowRateLimiter:
    def __init__(self, request_limit: int = 100, window_seconds: int = 60):
        self.request_limit = request_limit
        self.window_seconds = window_seconds

    async def check(self, tenant_id: str) -> RateLimitResult:
        key = f"cina:ratelimit:{tenant_id}:req"
        now = time.time()
        window_start = now - self.window_seconds

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)  # remove expired entries
        pipe.zcard(key)                                # count current entries
        pipe.zadd(key, {str(uuid4()): now})            # add this request
        pipe.expire(key, self.window_seconds + 1)      # auto-cleanup
        _, current_count, _, _ = await pipe.execute()

        remaining = max(0, self.request_limit - current_count)
        if current_count > self.request_limit:
            return RateLimitResult(
                allowed=False,
                limit=self.request_limit,
                remaining=0,
                reset_at=window_start + self.window_seconds,
            )
        return RateLimitResult(
            allowed=True,
            limit=self.request_limit,
            remaining=remaining,
            reset_at=now + self.window_seconds,
        )
```

Rate limit headers are injected into every response by the FastAPI middleware.

### 7.7 Prompt Version Routing

```python
class PromptRouter:
    async def select(self) -> PromptVersion:
        active_versions = await self.repo.get_active_versions()
        if not active_versions:
            raise NoActivePromptError()

        # Weighted random selection
        weights = [v.traffic_weight for v in active_versions]
        total = sum(weights)
        normalized = [w / total for w in weights]
        return random.choices(active_versions, weights=normalized, k=1)[0]
```

If only one version is active with weight 1.0, it's always selected (no A/B). When two versions are active (e.g., v1.2 at 0.8, v1.3 at 0.2), 20% of traffic hits the experimental prompt. Query logs record which version was used, enabling offline quality comparison.

---

## 8. Observability Architecture

### 8.1 Metrics Layer

```
┌────────────────────┐     ┌────────────────┐     ┌──────────────┐
│  CINA Services     │────▶│  Prometheus    │────▶│   Grafana    │
│  /metrics endpoint │     │  (scrape)      │     │  (dashboard) │
└────────────────────┘     └────────────────┘     └──────────────┘
```

All metrics are exposed via `prometheus_client` on `/metrics`. Prometheus scrapes at 15-second intervals (configurable). Grafana dashboards are defined as JSON and committed to the repo.

### 8.2 Structured Logging

All log entries are JSON-formatted with a consistent schema:

```json
{
    "timestamp": "2026-03-18T14:30:00.000Z",
    "level": "INFO",
    "service": "cina-query",
    "correlation_id": "query-abc123",
    "event": "rerank_completed",
    "data": {
        "candidates_in": 20,
        "candidates_out": 10,
        "latency_ms": 48,
        "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "device": "cuda"
    }
}
```

**Correlation ID propagation:** The `query_id` (generated at request entry) is threaded through every function call via a `contextvars.ContextVar`. All log entries and metrics labels within a request automatically include it. This enables reconstructing the full request lifecycle from logs.

```python
import contextvars

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id")

class CorrelationMiddleware:
    async def __call__(self, request, call_next):
        query_id = str(uuid4())
        token = correlation_id.set(query_id)
        request.state.query_id = query_id
        try:
            response = await call_next(request)
            return response
        finally:
            correlation_id.reset(token)
```

### 8.3 Dashboard Definitions

Four Grafana dashboards committed as JSON:

**Dashboard 1 — Ingestion Pipeline:**
- Documents processed over time (by source)
- Chunks created rate
- Embedding API latency (p50, p95, p99)
- Queue depth (pending documents)
- Error rate by stage
- Ingestion job status summary

**Dashboard 2 — Query Performance:**
- End-to-end latency distribution (p50, p95, p99)
- Latency breakdown by stage (search, rerank, assembly, LLM)
- Queries per second
- Chunks retrieved vs. chunks used (context efficiency)
- Token budget utilization histogram

**Dashboard 3 — Orchestration Health:**
- Cache hit rate over time
- Provider request rate (by provider, success/failure)
- Fallback trigger rate
- Circuit breaker state transitions
- Rate limit rejections by tenant

**Dashboard 4 — Cost Tracking:**
- Cost per query (p50, p95)
- Cumulative cost by provider
- Cost by tenant
- Token usage (input vs. output) by provider
- Cache savings estimate (cache hits × average query cost)

---

## 9. Configuration Management

### 9.1 Configuration Hierarchy

CINA uses a layered configuration approach:

```
Defaults (in code) ──▶ Config file (YAML) ──▶ Environment variables
                        (lowest priority)       (highest priority)
```

Environment variables override config file values. Config file values override code defaults. This enables Docker Compose to set defaults via a mounted config file while allowing per-container overrides via environment variables.

### 9.2 Configuration Schema

```yaml
# cina.yaml

ingestion:
  chunk:
    max_tokens: 512
    overlap_tokens: 64
    tokenizer: "cl100k_base"
    respect_sections: true
    sentence_alignment: true
  embedding:
    provider: "bedrock"  # "bedrock" (primary) — extensible via EmbeddingProviderProtocol
    model: "amazon.titan-embed-text-v2:0"
    dimensions: 512
    batch_size: 64
    max_retries: 3
    rate_limit_tpm: 5000000  # tokens per minute (Bedrock default is significantly higher)
    aws_region: "us-east-1"
  queue:
    backend: "redis"  # "redis" or "sqs"
    concurrency: 8
  sources:
    pubmed:
      enabled: true
      data_dir: "/data/pubmed"
    fda:
      enabled: true
      data_dir: "/data/fda"
    clinicaltrials:
      enabled: true
      api_base: "https://clinicaltrials.gov/api/v2"

serving:
  search:
    vector_top_k: 50
    bm25_top_k: 50
    rrf_k: 60
    ef_search: 100
  rerank:
    model: "cross-encoder/ms-marco-MiniLM-L-6-v2"
    candidates: 20
    top_n: 10
    device: "auto"  # "cuda", "cpu", or "auto"
  context:
    max_chunks: 15
    generation_buffer_tokens: 2048
  stream:
    keepalive_interval_seconds: 15

orchestration:
  providers:
    primary:
      name: "anthropic"
      model: "claude-sonnet-4-20250514"
      api_key_env: "ANTHROPIC_API_KEY"
      timeout_connect: 5.0
      timeout_read: 60.0
    fallback:
      name: "openai"
      model: "gpt-4o"
      api_key_env: "OPENAI_API_KEY"
      timeout_connect: 5.0
      timeout_read: 60.0
  fallback:
    ttft_threshold_seconds: 5.0
    circuit_breaker_failures: 3
    circuit_breaker_cooldown: 60
  cache:
    enabled: true
    num_hyperplanes: 16
    similarity_threshold: 0.95
    ttl_seconds: 86400
  rate_limit:
    requests_per_minute: 100
    tokens_per_hour: 100000
  prompt:
    default_version: "v1.0"

database:
  postgres:
    dsn_env: "DATABASE_URL"
    pool_min: 5
    pool_max: 20
  redis:
    url_env: "REDIS_URL"
    pool_max: 20

observability:
  log_level: "INFO"
  log_format: "json"
  metrics_port: 9090
  prometheus_path: "/metrics"
```

### 9.3 Environment Variable Mapping

Environment variables follow the pattern `CINA__{SECTION}__{KEY}` with double underscores as separators:

```bash
CINA__INGESTION__CHUNK__MAX_TOKENS=512
CINA__SERVING__RERANK__DEVICE=cuda
CINA__ORCHESTRATION__CACHE__ENABLED=true
```

Sensitive values (API keys, database credentials) are always environment variables, never in config files:

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
DATABASE_URL=postgresql://cina:password@localhost:5432/cina
REDIS_URL=redis://localhost:6379/0
```

---

## 10. Error Handling & Resilience

### 10.1 Error Classification

All errors are classified into two categories that determine handling:

**Retryable errors** (transient failures):
- Bedrock embedding throttling (ThrottlingException)
- Bedrock embedding service error (500, 502, 503)
- LLM provider timeout
- Database connection pool exhaustion
- Redis connection timeout

**Non-retryable errors** (permanent failures):
- Invalid document format (parsing failure)
- Chunk content exceeds maximum size
- Invalid API key (401)
- Malformed query (400)
- Context budget impossible (system prompt alone exceeds model limit)

### 10.2 Retry Strategy

Retryable errors use exponential backoff with jitter:

```python
async def retry_with_backoff(
    fn: Callable,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.5,
):
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except RetryableError as e:
            if attempt == max_retries:
                raise MaxRetriesExceeded(original=e, attempts=max_retries)
            delay = min(base_delay * (2 ** attempt), max_delay)
            delay += random.uniform(0, jitter * delay)
            await asyncio.sleep(delay)
```

### 10.3 Dead Letter Queue

Ingestion pipeline messages that fail after max retries are moved to a dead-letter queue (DLQ):

```
Main queue ──[max retries exceeded]──▶ DLQ
                                        │
                                   Manual inspection
                                   Fix and re-enqueue
```

The DLQ stores the original message, failure reason, timestamp, and attempt count. A CLI command (`cina dlq list`, `cina dlq retry`, `cina dlq purge`) enables manual inspection and re-processing.

### 10.4 Graceful Degradation in Serving Path

The serving path degrades gracefully rather than failing hard:

| Failure | Degradation |
|---------|------------|
| BM25 search fails | Return vector-only results (reduced quality, still functional) |
| Vector search fails | Return BM25-only results (no semantic matching, keyword-only) |
| Cross-encoder fails | Return RRF-ranked results without re-ranking (lower quality) |
| Primary LLM fails | Automatic fallback to secondary provider |
| Both LLMs fail | Return 503 with retrieved sources (client can display raw chunks) |
| Semantic cache fails | Skip cache, proceed to LLM (higher latency, still functional) |
| Redis entirely down | Rate limiting disabled, cache bypassed, proceed to LLM |

Each degradation is logged with the correlation ID and increments a Prometheus counter (`cina_degradation_total{component="bm25"}`) for alerting.

---

## 11. Security Considerations

### 11.1 Authentication

API access is authenticated via API keys passed in the `Authorization` header:

```
Authorization: Bearer cina_sk_...
```

API keys are stored as bcrypt hashes in PostgreSQL. Each key is associated with a tenant ID for rate limiting and cost tracking. Key management is via CLI (`cina apikey create`, `cina apikey revoke`).

### 11.2 Input Validation

All user input is validated before processing:

- **Query text:** Maximum 2,000 characters. Stripped of control characters. No injection risk — queries are passed as parameters to embedding APIs and as message content to LLM APIs, never interpolated into SQL or system commands.
- **Configuration overrides in query request:** Validated against allowed ranges (e.g., `max_sources` capped at 20, `stream` must be boolean).

### 11.3 Secret Management

- API keys (LLM providers, database) are injected via environment variables.
- No secrets in config files, code, or Docker images.
- Terraform uses variable references for secrets, with values passed via `-var-file` or environment at apply time.

### 11.4 Data Classification

CINA processes exclusively public medical literature. No Protected Health Information (PHI), no patient data, no private clinical records. This is stated explicitly in the PRD scope exclusions. The data pipeline is designed for public documents and makes no provisions for access control at the document level.

---

## 12. Local Development Architecture

### 12.1 Docker Compose Topology

```yaml
# docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: cina
      POSTGRES_USER: cina
      POSTGRES_PASSWORD: cina_dev
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes:
      - ./infra/prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
    volumes:
      - ./infra/grafana/dashboards:/var/lib/grafana/dashboards
      - ./infra/grafana/provisioning:/etc/grafana/provisioning

volumes:
  pgdata:
```

The CINA services themselves (ingestion worker, query server) run directly on the host during development — not in containers. This enables rapid iteration with hot reload (`uvicorn --reload`) and direct debugger attachment. Docker Compose provides only the infrastructure dependencies.

### 12.2 Development Workflow

```bash
# 1. Start infrastructure
docker compose up -d

# 2. Run migrations
cina db migrate

# 3. Ingest sample data
cina ingest --source pubmed --path ./data/sample/ --batch-size 16

# 4. Start query server (hot reload)
uvicorn cina.api:app --reload --port 8000

# 5. Test query
curl -N -X POST http://localhost:8000/v1/query \
  -H "Authorization: Bearer dev_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the contraindications for metformin in renal impairment?"}'
```

### 12.3 Development vs. Production Differences

| Component | Local (Docker Compose) | Production (AWS) |
|-----------|----------------------|------------------|
| Queue | Redis Streams | SQS |
| Database | PostgreSQL in Docker | RDS PostgreSQL |
| Cache | Redis in Docker | ElastiCache Redis |
| Object storage | Local filesystem (`./data/`) | S3 |
| Embeddings | Bedrock Titan V2 (same — AWS API call) | Same |
| Cross-encoder | CPU inference (slow, functional) | GPU inference (fast) |
| LLM provider | Same (direct API calls to Anthropic/OpenAI) | Same |
| Metrics | Prometheus + Grafana in Docker | Same (or CloudWatch) |

The queue abstraction (ADR-1) is the only component that switches implementation between environments. Bedrock embeddings use the same AWS API call in both environments — local development requires AWS credentials configured (IAM user or SSO). All other differences are configuration (connection strings, device selection).

---

## 13. Production Architecture (AWS)

### 13.1 AWS Resource Topology

```
┌─────────────────────────────────────────────────────────────┐
│                          VPC                                │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                  Private Subnets                     │   │
│  │                                                      │   │
│  │  ┌──────────────┐    ┌──────────────┐               │   │
│  │  │ ECS Fargate  │    │ ECS Fargate  │               │   │
│  │  │ Query Service│    │ Ingestion    │               │   │
│  │  │ (2 tasks)    │    │ Worker       │               │   │
│  │  └──────┬───────┘    └──────┬───────┘               │   │
│  │         │                   │                        │   │
│  │         ▼                   ▼                        │   │
│  │  ┌──────────────┐    ┌──────────────┐               │   │
│  │  │   RDS        │    │ ElastiCache  │               │   │
│  │  │ PostgreSQL   │    │   Redis      │               │   │
│  │  │ (pgvector)   │    │              │               │   │
│  │  │ db.t3.micro  │    │ t3.micro     │               │   │
│  │  └──────────────┘    └──────────────┘               │   │
│  │                                                      │   │
│  │  ┌──────────────┐    ┌──────────────┐               │   │
│  │  │     SQS      │    │      S3      │               │   │
│  │  │  + DLQ       │    │  (documents) │               │   │
│  │  └──────────────┘    └──────────────┘               │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                  Public Subnets                      │   │
│  │                                                      │   │
│  │  ┌──────────────┐                                   │   │
│  │  │     ALB      │◀── HTTPS (443)                    │   │
│  │  │              │                                   │   │
│  │  └──────────────┘                                   │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 13.2 Terraform Module Structure

```
infra/terraform/
├── main.tf
├── variables.tf
├── outputs.tf
├── terraform.tfvars.example
├── modules/
│   ├── vpc/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── ecs/
│   │   ├── main.tf          # Cluster, task defs, services
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── rds/
│   │   ├── main.tf          # PostgreSQL with pgvector
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── elasticache/
│   │   ├── main.tf          # Redis cluster
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── sqs/
│   │   ├── main.tf          # Queues + DLQ
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── s3/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── iam/
│       ├── main.tf          # Task roles, policies
│       ├── variables.tf
│       └── outputs.tf
```

### 13.3 Cost Estimate

| Resource | Spec | Estimated Monthly Cost |
|----------|------|----------------------|
| ECS Fargate (query) | 2 tasks, 0.5 vCPU, 1 GB RAM | ~$15 |
| ECS Fargate (ingestion) | 1 task, 0.25 vCPU, 0.5 GB RAM | ~$5 |
| RDS PostgreSQL | db.t3.micro, 20 GB gp3 | ~$15 |
| ElastiCache Redis | cache.t3.micro | ~$12 |
| SQS | Pay per message (negligible at demo scale) | ~$0.01 |
| S3 | < 2 GB storage | ~$0.05 |
| ALB | 1 LCU average | ~$16 |
| **Total (sustained)** | | **~$63/month** |
| **Demo cycle (2 hours)** | | **~$5–10** |

The demo cycle cost is the relevant number. Terraform apply, run the demo, capture evidence, Terraform destroy. Monthly cost is documented for reference but not incurred.

---

## 14. Package & Module Structure

```
cina/
├── __init__.py
├── __main__.py                    # CLI entry point
├── cli/
│   ├── __init__.py
│   ├── main.py                    # Click/Typer CLI app
│   ├── ingest.py                  # cina ingest commands
│   ├── serve.py                   # cina serve commands
│   ├── db.py                      # cina db migrate/reset
│   ├── apikey.py                  # cina apikey create/revoke
│   └── dlq.py                     # cina dlq list/retry/purge
├── api/
│   ├── __init__.py
│   ├── app.py                     # FastAPI application factory
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── query.py               # POST /v1/query
│   │   ├── health.py              # GET /health, GET /ready
│   │   └── metrics.py             # GET /metrics (Prometheus)
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── correlation.py         # Correlation ID injection
│   │   ├── auth.py                # API key validation
│   │   ├── rate_limit.py          # Rate limit header injection
│   │   └── error_handler.py       # Global exception handling
│   └── schemas/
│       ├── __init__.py
│       ├── query.py               # Request/response models
│       └── events.py              # SSE event models
├── ingestion/
│   ├── __init__.py
│   ├── pipeline.py                # Ingestion pipeline orchestrator
│   ├── connectors/
│   │   ├── __init__.py
│   │   ├── protocol.py            # SourceConnector protocol
│   │   ├── pubmed.py              # PubMed Central connector
│   │   ├── fda.py                 # FDA DailyMed connector
│   │   └── clinicaltrials.py      # ClinicalTrials.gov connector
│   ├── chunking/
│   │   ├── __init__.py
│   │   ├── engine.py              # Two-pass chunking engine
│   │   ├── sentences.py           # Medical-aware sentence splitter
│   │   └── config.py              # ChunkConfig dataclass
│   ├── embedding/
│   │   ├── __init__.py
│   │   ├── worker.py              # Async embedding worker
│   │   ├── protocol.py            # EmbeddingProviderProtocol
│   │   ├── bedrock.py             # Amazon Bedrock Titan V2 implementation
│   │   └── config.py              # EmbedConfig dataclass
│   └── queue/
│       ├── __init__.py
│       ├── protocol.py            # QueueProtocol definition
│       ├── redis_stream.py        # Redis Streams implementation
│       └── sqs.py                 # SQS implementation
├── serving/
│   ├── __init__.py
│   ├── pipeline.py                # Query pipeline orchestrator
│   ├── search/
│   │   ├── __init__.py
│   │   ├── vector.py              # pgvector dense retrieval
│   │   ├── bm25.py                # PostgreSQL full-text search
│   │   └── fusion.py              # Reciprocal Rank Fusion
│   ├── rerank/
│   │   ├── __init__.py
│   │   └── cross_encoder.py       # Cross-encoder re-ranker
│   ├── context/
│   │   ├── __init__.py
│   │   ├── assembler.py           # Token budget context assembly
│   │   └── prompt.py              # Prompt construction
│   └── stream/
│       ├── __init__.py
│       └── sse.py                 # SSE event formatting and streaming
├── orchestration/
│   ├── __init__.py
│   ├── middleware.py               # Middleware compose function
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── protocol.py            # LLMProviderProtocol
│   │   ├── openai.py              # OpenAI implementation
│   │   └── anthropic.py           # Anthropic implementation
│   ├── routing/
│   │   ├── __init__.py
│   │   ├── provider_router.py     # Health-aware provider selection
│   │   ├── circuit_breaker.py     # Circuit breaker state machine
│   │   ├── fallback.py            # Concurrent timeout fallback
│   │   └── prompt_router.py       # Weighted prompt version selection
│   ├── cache/
│   │   ├── __init__.py
│   │   ├── lsh.py                 # LSH hash computation
│   │   └── semantic_cache.py      # Cache lookup/store
│   └── limits/
│       ├── __init__.py
│       ├── rate_limiter.py        # Sliding window rate limiter
│       └── cost_tracker.py        # Per-query cost logging
├── db/
│   ├── __init__.py
│   ├── connection.py              # asyncpg pool management
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── document.py            # Document CRUD
│   │   ├── chunk.py               # Chunk CRUD + vector search
│   │   ├── query_log.py           # Query log writes
│   │   ├── cost_event.py          # Cost event writes
│   │   └── prompt_version.py      # Prompt version management
│   └── migrations/
│       ├── 001_initial_schema.sql
│       ├── 002_add_fts_indexes.sql
│       └── ...
├── models/
│   ├── __init__.py
│   ├── document.py                # Document, Section, Chunk dataclasses
│   ├── search.py                  # SearchResult, RankedResult
│   ├── query.py                   # QueryLog, CostEvent
│   ├── provider.py                # StreamChunk, CompletionConfig
│   └── cache.py                   # CachedResponse
├── config/
│   ├── __init__.py
│   ├── loader.py                  # YAML + env var config loading
│   └── schema.py                  # Pydantic settings models
└── observability/
    ├── __init__.py
    ├── metrics.py                 # Prometheus metric definitions
    └── logging.py                 # JSON structured logger setup
```

### 14.1 Key Design Decisions in Module Structure

**No circular dependencies.** The dependency graph is a DAG:
- `models/` depends on nothing (pure data structures)
- `db/` depends on `models/`
- `ingestion/`, `serving/`, `orchestration/` depend on `models/` and `db/`
- `api/` depends on `serving/` and `orchestration/`
- `cli/` depends on `ingestion/` and `api/`

**Protocols live with their consumers, not their implementors.** `QueueProtocol` lives in `ingestion/queue/protocol.py` because the ingestion pipeline defines what it needs from a queue. The Redis and SQS implementations import the protocol and implement it. This follows the Dependency Inversion Principle.

**No `utils/` or `helpers/` packages.** Every function belongs to a specific domain. Shared utilities (retry logic, token counting) live in the package that owns the concept.

---

## 15. Interface Contracts

### 15.1 External API

**POST /v1/query**

```
Request:
  Content-Type: application/json
  Authorization: Bearer <api_key>
  Body:
    query: str (required, max 2000 chars)
    config:
      provider: str (optional, "anthropic" | "openai")
      max_sources: int (optional, 1-20, default 10)
      stream: bool (optional, default true)
      temperature: float (optional, 0.0-1.0, default 0.3)

Response (stream=true):
  Content-Type: text/event-stream
  Events: metadata, token, citations, metrics, done
  (see Section 6.8 for full event schema)

Response (stream=false):
  Content-Type: application/json
  Body:
    query_id: str
    response: str
    citations: list[Citation]
    metrics: QueryMetrics
```

**GET /health**

```
Response:
  200 OK
  Body: {"status": "healthy"}

  503 Service Unavailable
  Body: {"status": "unhealthy", "checks": {"postgres": "down", "redis": "ok"}}
```

**GET /ready**

```
Response:
  200 OK (database connected, cross-encoder loaded, at least one LLM provider healthy)
  503 Not Ready (dependency check failed)
```

### 15.2 Internal Protocols

All internal protocols are defined as `typing.Protocol` classes. They serve as compile-time contracts enforced by mypy and as documentation of component boundaries.

```python
# Core protocols (summarized — full definitions in respective protocol.py files)

class SourceConnector(Protocol):
    """Ingestion: fetches and parses documents from a data source."""
    async def fetch_document_list(self, config: FetchConfig) -> AsyncIterator[RawDocument]: ...
    def parse(self, raw: RawDocument) -> Document: ...

class QueueProtocol(Protocol):
    """Ingestion: async message queue for document processing."""
    async def enqueue(self, message: dict, queue_name: str) -> str: ...
    async def dequeue(self, queue_name: str, timeout: int) -> dict | None: ...
    async def acknowledge(self, receipt: str) -> None: ...
    async def dead_letter(self, message: dict, queue_name: str, reason: str) -> None: ...

class EmbeddingProviderProtocol(Protocol):
    """Ingestion + Serving: generates embeddings from text via external API."""
    async def embed(self, texts: list[str], model: str, dimensions: int) -> list[list[float]]: ...
    async def health_check(self) -> bool: ...

class ChunkRepository(Protocol):
    """Shared: database operations for chunks and embeddings."""
    async def bulk_upsert(self, chunks: list[Chunk]) -> int: ...
    async def vector_search(self, embedding: list[float], top_k: int) -> list[SearchResult]: ...
    async def bm25_search(self, query: str, top_k: int) -> list[SearchResult]: ...

class Reranker(Protocol):
    """Serving: re-ranks search results by relevance."""
    async def rerank(self, query: str, candidates: list[SearchResult], top_n: int) -> list[SearchResult]: ...

class LLMProviderProtocol(Protocol):
    """Orchestration: streams completions from an LLM provider."""
    async def complete(self, messages: list[Message], config: CompletionConfig) -> AsyncIterator[StreamChunk]: ...
    async def health_check(self) -> bool: ...
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float: ...
```

---

## 16. Performance Budget

### 16.1 Query Path Latency Budget

Total p95 budget (excluding LLM generation): **500ms**

| Stage | p95 Target | Notes |
|-------|-----------|-------|
| Query embedding | 100ms | Single Bedrock API call, network-bound |
| Vector search (pgvector) | 50ms | HNSW index, ef_search=100, ~200k chunks |
| BM25 search (tsvector) | 30ms | GIN index, concurrent with vector |
| RRF fusion | 5ms | In-memory rank computation |
| Cross-encoder re-rank | 100ms | 20 candidates, GPU, MiniLM-L-6 |
| Context assembly | 10ms | In-memory token counting and packing |
| Cache lookup (LSH) | 5ms | Single Redis GET |
| Overhead (serialization, middleware) | 50ms | |
| **Total (pre-LLM)** | **350ms** | 150ms headroom under 500ms budget |

LLM time-to-first-token adds 500–2000ms depending on provider and load. Total end-to-end p95 target including LLM: **3 seconds**.

### 16.2 Ingestion Throughput Budget

| Stage | Target | Notes |
|-------|--------|-------|
| Document parsing | 200 docs/min | CPU-bound XML parsing, amortized |
| Chunking | 500 docs/min | CPU-bound, fast |
| Embedding (bottleneck) | 100 docs/min | API rate-limited via Bedrock, ~10 chunks/doc avg, 64-chunk batches |
| Database write | 1000 chunks/min | Bulk upserts, asyncpg |

The Bedrock embedding API is the bottleneck, though significantly less so than OpenAI. Bedrock's default TPM quota for Titan Embeddings V2 is substantially higher and can be increased via Service Quotas. For 5,000 documents averaging 10 chunks of 512 tokens each (25.6M tokens total), ingestion takes approximately 10–20 minutes at the default Bedrock TPM budget.

### 16.3 Resource Budget (Local Development)

| Resource | Budget |
|----------|--------|
| PostgreSQL memory | 256 MB (shared_buffers) |
| Redis memory | 128 MB |
| Cross-encoder model | ~250 MB GPU VRAM (MiniLM-L-6) |
| CINA query service | ~512 MB RSS |
| CINA ingestion worker | ~256 MB RSS |

Total local development footprint: ~1.5 GB RAM + GPU for cross-encoder.

---

## 17. Testing Architecture

### 17.1 Test Pyramid

```
                    ┌──────────┐
                    │   Load   │  k6 / locust
                    │   Tests  │  (sustained throughput, failover)
                    ├──────────┤
                 ┌──┤ Integr.  │  pytest + Docker Compose
                 │  │  Tests   │  (end-to-end pipeline, real DB)
                 │  ├──────────┤
              ┌──┤  │   Unit   │  pytest
              │  │  │  Tests   │  (chunking, RRF, budget, LSH)
              │  │  └──────────┘
              ▼  ▼
        (fast, many)  →  (slow, few)
```

### 17.2 Unit Tests

Pure functions with no I/O dependencies. Fast, deterministic, run on every commit.

**Chunking engine:**
- Fixed-window chunking produces correct token counts
- Structure-aware chunking respects section boundaries
- Overlap tokens are correctly computed
- Sentence boundary detection handles medical abbreviations
- Content hash is deterministic

**Reciprocal Rank Fusion:**
- Two identical lists produce expected fused ranking
- Disjoint lists interleave correctly
- Single-list input returns that list unchanged
- `k` parameter affects score distribution as expected

**Token budget manager:**
- Greedy packing respects budget limit
- Skip-and-try includes smaller chunks after a large one is skipped
- max_chunks hard cap is enforced
- Edge case: single chunk exceeds budget → empty context

**LSH hashing:**
- Same embedding always produces same hash
- Similar embeddings (cosine > 0.99) produce same hash with high probability
- Dissimilar embeddings produce different hashes with high probability
- Hyperplane count affects collision rate as expected

**Context assembly:**
- Citation indices are sequential starting at 1
- Metadata is correctly attached to each source
- System prompt + query + context fits within model limit

### 17.3 Integration Tests

Require running infrastructure (Docker Compose). Test component interactions with real databases and queues.

**Ingestion pipeline:**
- Ingest a known PubMed XML → verify document, section, and chunk records in PostgreSQL
- Ingest same document twice → verify idempotency (no duplicate chunks)
- Ingest malformed document → verify error handling and DLQ routing
- Verify pgvector HNSW index is queryable after ingestion

**Query path:**
- Ingest known documents → query → verify retrieved chunks are relevant
- Verify hybrid search returns results from both vector and BM25 paths
- Verify cross-encoder re-ranking changes result order
- Verify SSE stream produces valid event sequence (metadata → tokens → citations → metrics → done)

**Orchestration:**
- Verify cache stores and retrieves responses correctly
- Verify cache miss on dissimilar queries
- Verify rate limiter blocks requests over threshold
- Verify circuit breaker opens after consecutive failures (mock provider)

### 17.4 Load Tests

Run against the full local stack. Capture metrics in Prometheus/Grafana.

**Sustained throughput:**
- 10 concurrent users, 1 query/second each, for 5 minutes
- Measure p50, p95, p99 latency by stage
- Verify no memory leaks or connection pool exhaustion

**Cache warm-up:**
- Send 100 unique queries, then repeat them with minor variations
- Measure cache hit rate improvement over time

**Provider failover:**
- Simulate primary provider returning 500s mid-test
- Verify automatic fallback, measure failover latency

### 17.5 Benchmark Tests

Specific to CINA's retrieval quality. Not part of CI — run manually and results committed as evidence in ADRs.

**Chunking benchmark (ADR-6):**
- Ingest same corpus with structure-aware and fixed-window chunking
- Run a set of 50 curated clinical queries against each index
- Measure retrieval precision@10 and recall@10
- Document results in ADR-6

**Re-ranking benchmark (ADR-3):**
- Run same 50 queries with RRF-only vs. RRF + cross-encoder
- Measure nDCG@10 improvement
- Document latency tradeoff in ADR-3

---

## 18. Dependency Matrix

### 18.1 Python Dependencies

```
# Core
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
asyncpg>=0.29.0
redis[hiredis]>=5.0.0
pydantic>=2.9.0
pydantic-settings>=2.5.0
aioboto3>=13.0.0               # async AWS SDK (Bedrock embeddings, SQS)

# Embedding & ML
tiktoken>=0.7.0
numpy>=1.26.0
sentence-transformers>=3.0.0  # cross-encoder support
torch>=2.4.0                  # cross-encoder backend

# Document parsing
lxml>=5.2.0

# Observability
prometheus-client>=0.21.0
structlog>=24.4.0

# CLI
typer>=0.12.0
rich>=13.8.0                  # progress bars, tables

# Configuration
pyyaml>=6.0.0

# Testing
pytest>=8.3.0
pytest-asyncio>=0.24.0
httpx[http2]                   # async test client
locust>=2.31.0                 # load testing
```

### 18.2 Infrastructure Dependencies

```
PostgreSQL 16 + pgvector 0.7+
Redis 7+
Docker + Docker Compose v2
Terraform >= 1.8
AWS CLI v2 (required for Bedrock embeddings in all environments + production deployment)
```

### 18.3 External API Dependencies

```
Amazon Bedrock (embeddings)
  - amazon.titan-embed-text-v2:0 (ingestion + query embedding, dim 512)

OpenAI API (LLM completions — fallback)
  - gpt-4o (fallback LLM)

Anthropic API (LLM completions — primary)
  - claude-sonnet-4-20250514 (primary LLM)
```

### 18.4 Version Pinning Strategy

All dependencies are pinned to minimum compatible versions in `pyproject.toml`. Exact versions are locked via `uv.lock` (or `pip-compile` output) for reproducible builds. Dependabot or Renovate is configured for automated security updates.
