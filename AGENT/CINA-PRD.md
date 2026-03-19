# CINA — Clinical Index & Narrative Assembly

## Product Requirements Document

**Version:** 1.0
**Author:** Mehdi
**Date:** March 2026
**Status:** Pre-Implementation

---

## 1. Project Overview & Motivation

CINA is a backend system that ingests medical literature, builds a searchable embedding index, and serves a retrieval-augmented clinical reasoning API. The emphasis is entirely on infrastructure quality, not AI novelty.

### Portfolio Context

CINA is the third project in a portfolio engineered for zero overlap and complete backend pattern coverage:

| Dimension | FuelSense | AgriSense | CINA |
|-----------|-----------|-----------|------|
| Framework | Django | FastAPI + Julia | FastAPI async |
| Compute | PyTorch, OR-Tools | GPU Julia | LLM APIs, Bedrock embeddings, cross-encoder inference |
| Queue | Celery/Redis | — | Redis (local) / SQS (production) |
| Deploy | Kubernetes/Helm | Docker | ECS Fargate / Terraform |
| Pattern | Batch ML pipeline | Scientific compute | Real-time serving + streaming |
| Database | PostgreSQL | PostgreSQL | PostgreSQL + pgvector |

FuelSense demonstrates batch ML pipelines with a PyTorch TCN, training infrastructure, drift monitoring, and champion/challenger promotion. AgriSense demonstrates scientific compute with GPU-accelerated Julia. CINA fills the remaining gaps: embedding pipelines, vector search, async streaming, LLM orchestration, infrastructure-as-code, and real-time serving under latency constraints.

The target audience is backend engineering hiring managers. Every major backend pattern a recruiter might ask about — batch, compute, real-time — is represented across the three projects.

### What This Is Not

This is not a RAG tutorial. The standard RAG project is 50 lines of LangChain with Pinecone. CINA builds the infrastructure those frameworks abstract away. There is no LangChain, no LlamaIndex, no framework magic. Every component — chunking, hybrid search, re-ranking, context assembly, provider orchestration, caching — is implemented from scratch with explicit engineering decisions documented in ADRs.

---

## 2. Problem Statement

Clinical knowledge retrieval is a genuine unsolved infrastructure problem. Clinicians need answers grounded in peer-reviewed literature, not LLM hallucinations. The failure modes of naive retrieval-augmented generation in a medical context expose exactly the engineering challenges CINA addresses:

**Structured documents resist naive chunking.** Medical papers have semantically meaningful sections (Abstract, Methods, Results, Discussion). A fixed-window chunker that splits mid-section destroys context and produces chunks that mislead the retrieval layer. A production system needs structure-aware parsing that respects document topology.

**Pure vector search fails on exact-match clinical terms.** Drug names, dosages, gene identifiers, and ICD codes are precise tokens. Vector embeddings capture semantic similarity but routinely miss exact lexical matches that a clinician considers mandatory. Hybrid search combining dense vector similarity with sparse keyword scoring is required, not optional.

**Token budget management is a constraint satisfaction problem.** When assembling context from multiple retrieved chunks, you must pack the maximum relevant information into the LLM's context window without exceeding limits, while reserving space for the system prompt, query, and generation buffer. This is greedy bin-packing under constraints, not concatenation.

**Provider reliability and cost management are production concerns.** LLM APIs go down. Rate limits get hit. Costs scale with token volume. A production serving layer needs provider abstraction, automatic fallback routing, per-tenant rate limiting, cost tracking, and semantic caching to avoid redundant inference on near-duplicate queries.

The engineering challenge is the infrastructure beneath retrieval — not the retrieval concept itself.

---

## 3. Architecture Overview

CINA is composed of three independently deployable layers connected through well-defined interfaces:

```
┌─────────────────────────────────────────────────────────────────┐
│                     INGESTION PIPELINE                          │
│  Sources → Parsing → Chunking → Embedding → pgvector + metadata │
│  (async, event-driven, queue-backed)                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ populated index
┌──────────────────────────▼──────────────────────────────────────┐
│                     QUERY SERVING LAYER                          │
│  Query → Hybrid Search → Re-rank → Context Assembly → Stream    │
│  (real-time, latency-sensitive, SSE)                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ orchestrated through
┌──────────────────────────▼──────────────────────────────────────┐
│                   ORCHESTRATION LAYER                            │
│  Provider routing → Fallback → Prompt versioning → Cache → Rate │
│  (composable middleware, provider-agnostic)                      │
└─────────────────────────────────────────────────────────────────┘
```

**Architectural principle:** Each layer is independently deployable and testable. The ingestion pipeline can run without the serving layer. The serving layer can run against a pre-built index without the ingestion pipeline active. The orchestration layer is composable middleware injected into the serving path, not a monolith. This separation enables independent scaling, testing, and development.

**Communication boundaries:**

- Ingestion → Serving: shared PostgreSQL database with pgvector index. No runtime coupling.
- Serving → Orchestration: in-process function calls behind protocol interfaces. The orchestration layer is a set of composable middleware components (cache, rate limiter, provider router) that wrap the LLM call path.
- Queue interface: abstract protocol with two implementations (Redis for local development, SQS for production). The serving and ingestion layers are agnostic to the queue backend.

---

## 4. Component Specifications

### 4a. Document Ingestion Pipeline

The ingestion pipeline is an async worker system, not a script. It processes documents through a multi-stage pipeline with full metadata lineage tracking.

#### Source Connectors

Three public medical data sources, each with a dedicated connector:

- **PubMed Central Open Access Subset:** Full-text articles in structured XML (JATS format). Accessed via PMC OAI-PMH service or bulk FTP download. Sections are explicitly tagged (`<sec sec-type="methods">`, `<sec sec-type="results">`), enabling structure-aware chunking. Target: 2,000–5,000 articles across 3–4 therapeutic areas for demonstration.
- **FDA Drug Labels (DailyMed):** Structured Product Labeling (SPL) in XML format. Accessed via DailyMed API or bulk download. Contains sections like Indications, Dosage, Contraindications, Adverse Reactions. Target: 500–1,000 drug labels.
- **ClinicalTrials.gov:** Study records in JSON/XML format via the ClinicalTrials.gov API v2. Contains structured fields (eligibility criteria, interventions, outcomes). Target: 1,000–2,000 trial records.

All sources are public domain or open-access. No licensing restrictions apply.

#### Document Parsing & Normalization

Each source connector parses its native format and produces a common internal document representation:

```
Document:
  id: UUID
  source: enum(pubmed, fda, clinicaltrials)
  source_id: str  # PMC ID, DailyMed SetID, NCT number
  title: str
  authors: list[str]
  publication_date: date
  sections: list[Section]
  raw_metadata: dict  # source-specific fields preserved

Section:
  id: UUID
  document_id: UUID
  section_type: str  # "abstract", "methods", "results", "indications", etc.
  heading: str
  content: str
  order: int
```

This normalized representation is the contract between parsing and chunking. Adding a new source means implementing a new connector that produces `Document` objects — no downstream changes required.

#### Two-Pass Chunking Strategy

Medical documents have meaningful internal structure that naive fixed-window chunking destroys. CINA uses a two-pass strategy:

**First pass — structure-aware splitting:** Chunk boundaries align with document section boundaries. If a section fits within the configured chunk token budget (default: 512 tokens), it becomes a single chunk. Section metadata (type, heading, document context) is preserved on each chunk.

**Second pass — sliding window for oversized sections:** Sections exceeding the chunk token budget are split using a sliding window with configurable overlap (default: 64 tokens). Window boundaries are adjusted to avoid splitting mid-sentence using a sentence boundary detector.

This approach is a testable hypothesis. The PRD seeds ADR-6 with the expectation that benchmark results comparing structure-aware vs. naive fixed-window chunking on retrieval quality will be documented during Phase 1.

#### Chunk Schema

```
Chunk:
  id: UUID
  document_id: UUID
  section_id: UUID
  content: str
  token_count: int
  chunk_index: int  # position within the section
  overlap_tokens: int  # how many tokens overlap with previous chunk
  embedding: vector(dim)  # populated by embedding worker
  embedding_model: str  # e.g., "amazon.titan-embed-text-v2:0"
  embedding_dim: int  # e.g., 512
  created_at: timestamp
  metadata: dict  # section_type, heading, document title, authors, source
```

Full lineage is preserved: every chunk traces back to its section, document, and source. Embedding model version and dimensionality are stored per-chunk, enabling future model upgrades without re-indexing the entire corpus.

#### Batched Embedding Generation

Embedding generation runs as async workers consuming from the queue. Key design decisions:

- **Batching:** Chunks are grouped into batches (configurable, default: 64 chunks per API call) to amortize per-request overhead and stay within API rate limits.
- **Model:** Amazon Titan Embeddings V2 (`amazon.titan-embed-text-v2:0`) via Amazon Bedrock, with native 512-dimension output. Titan V2 supports configurable output dimensions (256, 512, 1024) natively, providing the same Matryoshka-style cost/quality tradeoff as OpenAI's dimension reduction but through a dedicated AWS service with higher default TPM quotas and ~6x lower per-token cost. The tradeoff and benchmark results will be documented in an ADR.
- **Embedding provider abstraction:** Embedding generation is mediated through an `EmbeddingProviderProtocol`, with a Bedrock implementation as the primary path. This mirrors the multi-provider pattern used for LLM inference and allows future provider swaps without downstream changes.
- **Retry logic:** Exponential backoff with jitter on API failures. Dead-letter queue for chunks that fail after max retries.
- **Idempotency:** Chunks are keyed by content hash + model version. Re-running ingestion on the same document skips already-embedded chunks.

#### Queue Abstraction

The ingestion pipeline communicates through an abstract queue protocol:

```python
class QueueProtocol(Protocol):
    async def enqueue(self, message: dict, queue_name: str) -> str: ...
    async def dequeue(self, queue_name: str, timeout: int) -> dict | None: ...
    async def acknowledge(self, receipt: str) -> None: ...
    async def dead_letter(self, message: dict, queue_name: str, reason: str) -> None: ...
```

Two implementations:

- **RedisQueue:** Uses Redis streams for local development. Already in the compose stack for caching and rate limiting — zero additional infrastructure.
- **SQSQueue:** Uses AWS SQS with DLQ configuration for production. Terraform-provisioned.

The interface abstraction is a deliberate engineering decision (ADR-1). It enables full integration testing locally while proving the production path works on AWS via the recorded demo.

#### Storage

- **pgvector** on PostgreSQL for chunk embeddings with HNSW indexing (configurable `ef_construction` and `m` parameters).
- **PostgreSQL relational tables** for document metadata, section metadata, chunk metadata, and ingestion audit logs.
- **S3** (production) or local filesystem (development) for raw source document archival.

---

### 4b. Query Serving Layer

The serving layer handles real-time clinical queries with a multi-stage retrieval and response pipeline. This is where the real-time serving gap in the portfolio is filled.

#### Hybrid Search

Pure vector search misses exact lexical matches that are critical in medical contexts (drug names, dosages, gene identifiers, ICD codes). CINA combines two retrieval strategies:

- **Dense retrieval:** Query embedding via the same model used for indexing (Titan Embeddings V2, dim 512), cosine similarity search against the pgvector HNSW index. Returns top-k candidates (configurable, default: 50).
- **Sparse retrieval (BM25):** Full-text keyword search using PostgreSQL's built-in `tsvector`/`tsquery` with `ts_rank_cd` scoring. Configured with a medical-domain stop word list. Returns top-k candidates (configurable, default: 50).

Results are merged using **Reciprocal Rank Fusion (RRF)** as the initial fusion step before re-ranking:

```
RRF_score(chunk) = Σ 1 / (k + rank_in_list)
```

where `k` is a smoothing constant (default: 60). RRF is rank-based, not score-based, so it handles the incomparable scales of cosine similarity and BM25 scores naturally. The fused list is truncated to top-n candidates (default: 20) for re-ranking.

#### Cross-Encoder Re-Ranking

The top-n candidates from RRF are re-ranked using a cross-encoder model (`cross-encoder/ms-marco-MiniLM-L-6-v2` as baseline, with the option to benchmark larger models). The cross-encoder scores each (query, chunk) pair for relevance, producing a final ranked list.

This is the most computationally expensive step in the serving path. Design decisions:

- **Candidate count controls latency:** Re-ranking 20 candidates with a MiniLM cross-encoder takes ~50ms on GPU. This is within budget for a real-time API. Re-ranking 100 candidates pushes toward 200ms. The n parameter is tunable.
- **Model loading:** The cross-encoder is loaded once at service startup and held in memory. This means the serving tier is stateful with respect to the model — documented in ADR-3 with the justification that the relevance improvement over pure RRF warrants the memory footprint.
- **GPU requirement:** Cross-encoder inference runs on GPU for acceptable latency. CPU inference is ~10x slower and only suitable for development/testing.

#### Context Assembly with Token Budget Management

After re-ranking, the top chunks are assembled into a context window for the LLM. This is a constraint satisfaction problem:

```
total_budget = model_context_limit  # e.g., 128k tokens
reserved = system_prompt_tokens + query_tokens + generation_buffer
available = total_budget - reserved
```

Chunks are packed greedily in rank order until the available budget is exhausted. Partial chunks are not included — if a chunk doesn't fit, it's skipped and the next smaller chunk is tried. Each included chunk carries its citation metadata (document title, authors, source ID, section type) for downstream citation injection.

The generation buffer (default: 2048 tokens) reserves space for the model's response. The system prompt is versioned (see Orchestration Layer § Prompt Versioning).

#### Streaming SSE Response

The query endpoint returns a Server-Sent Events stream:

```
POST /v1/query
Content-Type: application/json

{
  "query": "What are the contraindications for metformin in patients with renal impairment?",
  "config": {
    "provider": "anthropic",       // optional, uses default routing
    "max_sources": 10,             // max chunks to include in context
    "stream": true                 // SSE streaming (default: true)
  }
}

Response: text/event-stream

event: metadata
data: {"query_id": "...", "sources_used": 7, "model": "claude-sonnet-4-20250514"}

event: token
data: {"text": "Metformin"}

event: token
data: {"text": " is contraindicated"}

...

event: citations
data: {"citations": [{"index": 1, "document": "...", "section": "contraindications", "source_id": "PMC..."}]}

event: done
data: {"total_tokens": 847, "latency_ms": 1230}
```

The SSE stream proxies the LLM provider's streaming response while injecting CINA-specific metadata (source citations, cost tracking, latency). The client receives tokens as they're generated — no buffering the full response.

#### Source Citation Injection

Every claim in the generated response is traceable to retrieved chunks. The citation mechanism:

1. The system prompt instructs the LLM to reference sources by index number (e.g., `[1]`, `[2]`).
2. The context assembly step numbers each included chunk and provides them in the prompt as numbered sources.
3. The `citations` SSE event at the end of the stream maps each index to the full source metadata.

This enables the client to render inline citations linked to original documents. The citation accuracy (whether the model actually cites the correct source for each claim) is a quality metric tracked via prompt versioning experiments.

---

### 4c. LLM Orchestration Layer

The orchestration layer sits between the query serving logic and the LLM providers. It is implemented as composable middleware components that wrap the LLM call path.

#### Provider Abstraction

A unified interface for LLM providers:

```python
class LLMProviderProtocol(Protocol):
    async def complete(
        self,
        messages: list[Message],
        config: CompletionConfig,
    ) -> AsyncIterator[StreamChunk]: ...

    async def health_check(self) -> bool: ...

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float: ...
```

Implementations for OpenAI and Anthropic. Adding a new provider means implementing this interface — no changes to the serving layer.

#### Fallback Routing

If the primary provider errors or exceeds a latency threshold, CINA automatically falls back to the secondary provider. This mirrors the fallback pattern from FuelSense (ADR-3 in that project) — the reuse is deliberate and the PRD should reference it as a portfolio callback.

Fallback logic:

- **Health check:** Periodic background health checks (configurable interval, default: 30s) maintain a provider health registry.
- **Circuit breaker:** After N consecutive failures (default: 3), a provider is marked unhealthy for a cooldown period (default: 60s). Requests route to the next healthy provider.
- **Timeout fallback:** If the primary provider's time-to-first-token exceeds a threshold (default: 5s), the request is concurrently sent to the fallback provider. The first response wins; the other is cancelled.

#### Prompt Versioning & A/B Routing

System prompts are versioned and stored in configuration (not hardcoded). Each prompt version has:

```
PromptVersion:
  id: str  # e.g., "v1.2"
  system_prompt: str
  description: str
  created_at: timestamp
  active: bool
  traffic_weight: float  # 0.0–1.0 for A/B routing
```

When multiple versions are active, requests are routed probabilistically by traffic weight. Each query logs which prompt version was used, enabling offline quality comparison. This is infrastructure for experimentation, not a deployed A/B testing platform — the point is showing you know how to build it.

#### Semantic Cache

Near-duplicate queries should hit a cache instead of incurring another LLM inference. CINA uses a locality-sensitive hashing (LSH) approach for O(1) Redis lookup:

1. The query embedding (already computed for search) is hashed using a set of random hyperplane projections into a binary hash.
2. The hash is used as a Redis key prefix. A small number of candidate cached responses are retrieved.
3. Candidates are checked against a cosine similarity threshold (default: 0.95). If a match is found, the cached response is returned directly.
4. Cache entries have a TTL (default: 24h) and are tagged with the prompt version that generated them. Prompt version changes invalidate relevant cache entries.

This avoids building a second vector index for the cache. The LSH parameters (number of hyperplanes, hash length) control the precision/recall tradeoff of cache hits and are configurable.

#### Per-Tenant Rate Limiting

Redis-backed sliding window rate limiter:

- Keyed by API key or tenant identifier.
- Configurable limits per time window (e.g., 100 queries/minute, 10,000 tokens/hour).
- Token-based limiting tracks both input and output tokens against a budget.
- Rate limit headers returned on every response (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`).

#### Cost Tracking

Every LLM call logs:

```
CostEvent:
  query_id: UUID
  tenant_id: str
  provider: str
  model: str
  input_tokens: int
  output_tokens: int
  estimated_cost_usd: float
  cache_hit: bool
  timestamp: datetime
```

Aggregated by tenant and provider for dashboarding. Enables cost-per-query and cost-per-tenant reporting.

---

### 4d. Observability

Prometheus metrics exposed on `/metrics` for all services:

**Ingestion pipeline:**
- `cina_ingestion_documents_processed_total` (counter, by source)
- `cina_ingestion_chunks_created_total` (counter)
- `cina_ingestion_embedding_latency_seconds` (histogram)
- `cina_ingestion_embedding_batch_size` (histogram)
- `cina_ingestion_errors_total` (counter, by stage and error type)
- `cina_ingestion_queue_depth` (gauge)

**Query serving:**
- `cina_query_latency_seconds` (histogram, by stage: search, rerank, assembly, llm)
- `cina_query_total` (counter)
- `cina_search_results_count` (histogram, by method: vector, bm25, fused)
- `cina_rerank_latency_seconds` (histogram)
- `cina_context_tokens_used` (histogram)
- `cina_context_chunks_included` (histogram)

**Orchestration:**
- `cina_cache_hit_total` / `cina_cache_miss_total` (counters)
- `cina_provider_request_total` (counter, by provider and status)
- `cina_provider_latency_seconds` (histogram, by provider)
- `cina_provider_fallback_total` (counter)
- `cina_rate_limit_exceeded_total` (counter, by tenant)
- `cina_cost_usd_total` (counter, by provider and tenant)

**Grafana dashboards:**
- Ingestion pipeline throughput and error rates
- Query latency breakdown by stage (p50, p95, p99)
- Cache hit rate over time
- Provider health and fallback frequency
- Cost accumulation by provider and tenant

**Structured logging:** JSON-formatted logs with correlation IDs (query_id propagated through all stages). Enables request tracing from SSE response back through re-ranking, search, and cache lookup.

---

## 5. Data Sources

### PubMed Central Open Access Subset

- **Format:** JATS XML (structured sections, figures, tables, references)
- **Access:** PMC OAI-PMH service or FTP bulk download (`ftp.ncbi.nlm.nih.gov/pub/pmc/oa_bulk/`)
- **License:** Open access (CC BY, CC0, or similar). PMC OA subset is explicitly cleared for text mining.
- **Volume for demo:** 2,000–5,000 full-text articles. Curate across 3–4 therapeutic areas (e.g., cardiology, oncology, infectious disease, endocrinology) to demonstrate cross-domain retrieval.
- **Size estimate:** ~50–200 KB per article XML. Total corpus: ~500 MB–1 GB raw.

### FDA Drug Labels (DailyMed)

- **Format:** Structured Product Labeling (SPL) XML
- **Access:** DailyMed web services API or bulk download
- **License:** Public domain (US government work)
- **Volume for demo:** 500–1,000 drug labels covering common therapeutic classes.
- **Size estimate:** ~50–500 KB per label. Total corpus: ~100–250 MB raw.

### ClinicalTrials.gov

- **Format:** JSON via ClinicalTrials.gov API v2, or XML bulk download
- **Access:** REST API with pagination, or bulk download
- **License:** Public domain
- **Volume for demo:** 1,000–2,000 study records. Focus on completed studies with results posted.
- **Size estimate:** ~10–50 KB per record. Total corpus: ~50–100 MB raw.

### Total Corpus

Approximately 3,500–8,000 documents producing an estimated 50,000–200,000 chunks. This is sufficient to demonstrate the architecture at meaningful scale without requiring enterprise-grade infrastructure.

---

## 6. Tech Stack & Justification

| Technology | Role | Justification |
|-----------|------|---------------|
| **FastAPI** | Web framework, async serving | Not Django — proves framework range. Native async/await fits streaming SSE and concurrent I/O. |
| **Python 3.12+ (async)** | Primary language | Async-first architecture. `asyncio` throughout for I/O-bound operations. |
| **PostgreSQL + pgvector** | Vector store + relational metadata | Extends existing Postgres expertise into vector search. Single database for both embeddings and metadata — no separate vector DB to operate. HNSW indexing for approximate nearest neighbor search. |
| **PostgreSQL tsvector/tsquery** | BM25 keyword search | Built-in full-text search. No additional infrastructure for the sparse retrieval path. |
| **Redis** | Task queue (local), semantic cache, rate limiting | Triple duty from a single dependency. Redis Streams for local queue implementation. LSH-keyed cache entries. Sliding window rate limiter. |
| **cross-encoder/ms-marco-MiniLM-L-6-v2** | Re-ranking model | Small enough for GPU serving with acceptable latency. Benchmark against larger cross-encoders during Phase 2. |
| **Amazon Bedrock — Titan Embeddings V2 (dim 512)** | Embedding model | AWS-native embedding service with ~6x lower cost than OpenAI, higher TPM quotas, and native 512-dim output. Deepens AWS integration (Bedrock joins SQS, S3, ECS, RDS in the stack). Accessed via `aioboto3` — same SDK already used for SQS. |
| **Docker Compose** | Local development | Stands up PostgreSQL (pgvector), Redis, and CINA services. First-class local dev experience — what a reviewer actually clones and runs. |
| **Terraform** | Infrastructure as Code | New skill with massive demand in job postings. Provisions ECS, RDS, SQS, S3, ElastiCache, Bedrock access. Full apply/destroy lifecycle. |
| **ECS Fargate** | Production container orchestration | Different deployment pattern from FuelSense's Kubernetes — proves range. Serverless containers, no cluster management. |
| **SQS** | Production message queue | AWS-native queue replacing the local Redis Streams path. DLQ support. Terraform-provisioned. |
| **S3** | Raw document archival | Source documents stored for reprocessing and audit. |
| **Prometheus** | Metrics collection | Reuses existing expertise in a new context (LLM-specific metrics, cache hit rates, cost tracking). |
| **Grafana** | Dashboarding | Visual proof of system behavior under load. Captured in demo screenshots. |

### Deliberate Exclusions from Stack

| Excluded | Reason |
|----------|--------|
| **LangChain / LlamaIndex** | Building the infrastructure these frameworks abstract is the entire point of CINA. Using them would eliminate the portfolio signal. |
| **Pinecone / Weaviate / Qdrant** | pgvector on PostgreSQL is sufficient at this scale and demonstrates operational simplicity. A separate vector DB adds cost and operational burden for zero portfolio value at demo scale. |
| **Self-hosted LLM** | Adds GPU cost and operational burden. The cross-encoder is the only local model, and it's small. LLM inference is via API — the orchestration layer is the interesting part, not hosting a model. |
| **Django** | FuelSense already demonstrates Django expertise. Repeating it signals narrowness. |
| **Celery** | FuelSense already demonstrates Celery. The Redis Streams / SQS abstraction shows a different queuing pattern. |

---

## 7. Architecture Decision Records

Each ADR follows the format: **Context → Decision → Consequences.** ADRs are seeded here and will be expanded with implementation evidence during development.

### ADR-1: Queue Abstraction — Redis Streams (Local) vs. SQS (Production)

**Context:** The ingestion pipeline needs an async task queue. FuelSense uses Celery/Redis. Using the same stack here adds no portfolio signal. The local development story must be first-class (Docker Compose, no AWS dependency), while the production path must be AWS-native.

**Decision:** Define an abstract `QueueProtocol` with two implementations: `RedisStreamQueue` for local development (Redis is already in the compose stack for caching and rate limiting) and `SQSQueue` for production (Terraform-provisioned). The interface includes `enqueue`, `dequeue`, `acknowledge`, and `dead_letter` operations.

**Consequences:** Full integration testing runs locally with no AWS dependency. The production path is proven via the recorded Terraform apply/destroy demo. The protocol pattern demonstrates interface-driven design — the consuming code is agnostic to the queue backend.

### ADR-2: Hybrid Search — BM25 + Vector, Not Vector Alone

**Context:** Pure vector search captures semantic similarity but misses exact lexical matches critical in medical contexts (drug names like "metformin", dosages like "500mg", gene identifiers like "BRCA1", ICD codes). A clinician searching for "metformin contraindications" expects results containing the exact word "metformin", not just semantically similar terms.

**Decision:** Combine dense vector retrieval (pgvector cosine similarity) with sparse keyword retrieval (PostgreSQL tsvector/tsquery with BM25-style scoring). Merge results using Reciprocal Rank Fusion before re-ranking.

**Consequences:** Two retrieval paths to maintain and tune. Additional PostgreSQL full-text search indexes on chunk content. RRF fusion adds negligible latency. The payoff is measurably better retrieval on medical queries containing precise terminology — benchmark results will be included in this ADR.

### ADR-3: Re-Ranking — Cross-Encoder over Reciprocal Rank Fusion Alone

**Context:** RRF produces a fused ranking from the vector and BM25 retrieval paths. This ranking is decent but not optimal — it's based on rank position, not actual query-document relevance. Cross-encoder models score (query, document) pairs jointly and produce significantly better relevance judgments, at the cost of loading a model on the serving tier and adding ~50ms latency for 20 candidates on GPU.

**Decision:** Use a cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) for re-ranking the top-20 RRF candidates. The model is loaded at service startup and held in GPU memory. Development and testing can use CPU inference with relaxed latency expectations.

**Consequences:** The serving tier is stateful with respect to the cross-encoder model. GPU is required for production-grade latency. This is a stronger portfolio signal than pure RRF — it demonstrates integrating a real inference model into a latency-sensitive serving path. Benchmark results comparing RRF-only vs. cross-encoder re-ranking on retrieval quality and latency will be included.

### ADR-4: Semantic Cache — LSH-Keyed Redis, Not a Second Vector Index

**Context:** Semantically similar queries (e.g., "metformin side effects" and "adverse reactions of metformin") should hit a cache instead of incurring another LLM inference. The naive approach is building a second vector index over cached query embeddings and searching it on every request. This adds a vector search to every cache lookup — defeating the purpose.

**Decision:** Use locality-sensitive hashing (random hyperplane projection) to convert query embeddings into binary hash keys. Store cached responses in Redis keyed by the LSH hash. Cache lookup is O(1) Redis GET, not a vector search. A cosine similarity check against a small number of hash-collision candidates confirms the match.

**Consequences:** Cache hit rate depends on LSH parameters (number of hyperplanes, hash length). Tuning these requires experimentation. The tradeoff is a small false-negative rate (similar queries that hash differently) in exchange for constant-time lookup. This is a cleaner architecture than a second vector index and a more interesting engineering story.

### ADR-5: Provider Fallback — Circuit Breaker with Concurrent Timeout Fallback

**Context:** LLM APIs have non-trivial failure rates and latency variance. FuelSense's ADR-3 established a fallback pattern for model serving. CINA reuses and extends this pattern for multi-provider LLM orchestration.

**Decision:** Implement a circuit breaker per provider (open after N consecutive failures, half-open after cooldown). Add concurrent timeout fallback: if the primary provider's time-to-first-token exceeds 5 seconds, concurrently send the request to the fallback provider and return whichever responds first. This is an explicit evolution of the FuelSense fallback pattern — same principle (no single point of failure), more sophisticated implementation (concurrent racing, circuit breaker state machine).

**Consequences:** More complex provider management logic. Potential for duplicate LLM calls (and costs) during timeout races — mitigated by cancellation of the losing request. The deliberate portfolio callback from FuelSense shows pattern evolution across projects.

### ADR-6: Chunking Strategy — Structure-Aware vs. Naive Fixed-Window

**Context:** Medical papers have semantically meaningful sections. A fixed-window chunker that splits at arbitrary token boundaries produces chunks that straddle section boundaries, mixing methods with results or abstract with introduction.

**Decision:** Two-pass chunking. First pass: respect document section boundaries (never split a section that fits within the token budget). Second pass: sliding window with overlap (default 64 tokens) for oversized sections, with sentence-boundary alignment.

**Consequences:** Requires source-format-specific parsers to extract section structure (JATS XML for PubMed, SPL XML for FDA labels). More complex than naive chunking, but produces semantically coherent chunks. Retrieval quality benchmark comparing structure-aware vs. fixed-window chunking will be the primary evidence in this ADR.

---

## 8. Phase Breakdown

### Phase 1: Ingestion Pipeline

**Goal:** End-to-end document ingestion from raw source to searchable pgvector index.

**Deliverables:**
- Source connectors for PubMed Central, FDA DailyMed, ClinicalTrials.gov
- Document parsing and normalization to common representation
- Two-pass chunking implementation with configurable parameters
- Batched embedding generation via Amazon Bedrock (Titan Embeddings V2) with retry logic and idempotency
- pgvector storage with HNSW index and full metadata schema
- Redis Streams queue implementation (local path)
- CLI tool: `cina ingest --source pubmed --path ./data/pubmed/ --batch-size 64`
- Integration tests: ingest a known set of documents, verify chunk count, metadata integrity, embedding dimensions
- Chunking benchmark: structure-aware vs. fixed-window retrieval quality comparison (seeds ADR-6)

**Infrastructure:**
- Docker Compose: PostgreSQL 16 with pgvector extension, Redis 7
- Development on Vast.ai CPU instance

**Exit criteria:** CLI ingests 1,000 PubMed articles, producing correctly chunked and embedded entries in pgvector. Metadata lineage is verifiable from any chunk back to source document.

---

### Phase 2: Query Serving Layer

**Goal:** Real-time query endpoint with hybrid search, cross-encoder re-ranking, context assembly, and streaming SSE.

**Deliverables:**
- FastAPI application with async request handling
- Hybrid search: pgvector cosine similarity + PostgreSQL full-text search
- Reciprocal Rank Fusion implementation
- Cross-encoder re-ranking integration (loaded at startup, GPU inference)
- Token budget manager with greedy chunk packing
- Streaming SSE endpoint with metadata, token, citation, and done events
- Source citation injection (numbered references in prompt, citation metadata in SSE stream)
- Query latency benchmarks: p50, p95, p99 broken down by stage
- Re-ranking benchmark: RRF-only vs. cross-encoder on retrieval quality (seeds ADR-3)

**Infrastructure:**
- Vast.ai GPU instance (RTX 5090 or equivalent) for cross-encoder development and benchmarking
- Same Docker Compose stack plus FastAPI service

**Exit criteria:** `POST /v1/query` accepts a clinical question, streams a cited response in under 2 seconds p95 (excluding LLM generation time), and returns accurate source citations.

---

### Phase 3: Orchestration Hardening

**Goal:** Production-grade middleware transforming a working prototype into a resilient, observable system.

**Deliverables:**
- LLM provider abstraction with OpenAI and Anthropic implementations
- Circuit breaker with concurrent timeout fallback
- Prompt versioning with traffic-weighted A/B routing
- LSH-based semantic cache with configurable parameters and TTL
- Per-tenant rate limiting (sliding window, token-based)
- Cost tracking and aggregation
- Prometheus metrics for all components (ingestion, serving, orchestration)
- Grafana dashboard definitions (JSON exports in repo)
- Structured JSON logging with correlation ID propagation
- Load testing with `locust` or `k6`: sustained query throughput, cache warm-up, provider failover scenarios

**Infrastructure:**
- Same Docker Compose stack plus Prometheus and Grafana containers

**Exit criteria:** System handles sustained query load with observable metrics. Provider failover demonstrated (simulate primary failure, verify automatic fallback). Cache hit rate > 50% on repeated similar queries. Rate limiting enforced correctly. Dashboards capture all key metrics.

---

### Phase 4: AWS Deployment Proof

**Goal:** Demonstrate production-grade AWS deployment via Terraform, capture evidence, tear down.

**Deliverables:**
- Terraform modules: VPC, ECS Fargate cluster, RDS PostgreSQL (db.t3.micro) with pgvector, ElastiCache Redis, SQS queues with DLQ, S3 bucket, IAM roles, security groups
- ECS task definitions for ingestion worker and query service
- SQS queue implementation (production path of queue abstraction)
- `terraform plan` output saved as text artifact
- Recorded demo session (asciinema or video, 3–5 minutes):
  - `terraform apply` provisioning all infrastructure
  - Ingestion pipeline processing a batch of documents via SQS
  - Query endpoint serving a clinical question with streaming response
  - Grafana dashboard screenshots under load
  - `terraform destroy` tearing everything down
- Cost estimate documentation (what the demo cost, what sustained operation would cost)

**Infrastructure:**
- AWS account with temporary resources
- Estimated demo cost: $5–10 for a single apply/demo/destroy cycle

**Exit criteria:** Full apply/destroy cycle recorded. Demo shows end-to-end flow on live AWS infrastructure. All Terraform modules in repo and validated.

---

## 9. Scope Exclusions

| Excluded | Rationale |
|----------|-----------|
| **Frontend / Chat UI** | CINA is a backend project. The API and SSE stream are the deliverables. A frontend adds no backend engineering signal. |
| **Model fine-tuning** | FuelSense covers the training story (PyTorch TCN, champion/challenger). CINA demonstrates inference orchestration, not training. |
| **LangChain / LlamaIndex** | Building the infrastructure these frameworks abstract is the point. Using them eliminates the portfolio signal. |
| **Self-hosted LLMs** | Adds GPU cost and operational burden for zero portfolio value. The orchestration layer (provider abstraction, fallback, caching) is the interesting part. |
| **HIPAA compliance** | CINA ingests public medical literature (PubMed, FDA labels, ClinicalTrials.gov), not patient data. No PHI is processed, stored, or transmitted. Stating this explicitly preempts the question. |
| **Multi-tenancy at scale** | Rate limiting and cost tracking are per-tenant, but this is a demonstration, not a SaaS platform. No tenant isolation, billing integration, or admin UI. |

---

## 10. Success Criteria

### Quantitative

| Metric | Target |
|--------|--------|
| Ingestion throughput | ≥ 100 documents/minute sustained (PubMed articles, batched embedding via Bedrock) |
| Chunk metadata integrity | 100% of chunks trace back to source document, section, and embedding model version |
| Query latency (excl. LLM generation) | p95 < 500ms (search + rerank + context assembly) |
| End-to-end query latency (incl. LLM TTFT) | p95 < 3 seconds |
| Cross-encoder re-rank latency | p95 < 100ms for 20 candidates on GPU |
| Cache hit rate | > 50% on repeated semantically similar query workload |
| Provider fallback | Zero-downtime demonstrated: primary failure → automatic fallback, query succeeds |
| Terraform lifecycle | Clean apply and destroy with zero manual intervention |

### Qualitative

- **Code quality:** Type-annotated Python throughout. Async-first. Protocol-based interfaces. No framework magic.
- **Documentation:** README with architecture diagram, quickstart (Docker Compose), API reference, and link to recorded demo. ADRs with benchmark evidence.
- **Test coverage:** Unit tests for chunking, RRF, token budget manager, LSH cache. Integration tests for ingestion pipeline and query path. Load tests for sustained throughput.
- **Portfolio coherence:** A reviewer examining all three projects sees deliberate non-overlap, pattern evolution (FuelSense fallback → CINA circuit breaker), and increasing infrastructure sophistication.
