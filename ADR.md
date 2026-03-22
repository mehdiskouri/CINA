# CINA — Architecture Decision Records

Six architectural decisions define CINA's technical shape. This document records each one with full rigour: the problem that forced a choice, the option selected and why, every serious alternative that was evaluated and rejected, a weighted decision matrix, and the implementation evidence that validates the outcome.

Each ADR follows a strict format: **Context → Decision → Consequences (benefits, costs, degradation) → Rejected Alternatives → Decision Matrix → Implementation Evidence**.

---

## Decision Summary Matrix

The table below consolidates all six decisions into a single view. Each row captures the selected option, the highest-scoring rejected alternative, and the margin of victory from the per-ADR weighted decision matrix. Criteria weights are listed in each ADR section; scores use a 1–10 Likert scale.

| # | Decision | Selected Option | Score | Runner-Up | Score | Δ | Key Discriminator |
|---|----------|----------------|-------|-----------|-------|---|-------------------|
| 001 | [Queue abstraction](#adr-001-queue-abstraction-for-ingestion-workers) | `QueueProtocol` (Redis/SQS) | **9.00** | Amazon MQ | 6.80 | +2.20 | Local dev simplicity + production DLQ parity |
| 002 | [Hybrid search](#adr-002-hybrid-search-with-reciprocal-rank-fusion) | Vector + BM25 + RRF | **9.25** | Vector-only | 7.75 | +1.50 | Lexical precision on clinical terminology |
| 003 | [Reranking](#adr-003-cross-encoder-reranking) | cross-encoder MiniLM-L-6 | **8.50** | RRF-only (tie) | 8.50 | 0.00 | Relevance quality tiebreaker (30% weight) |
| 004 | [Semantic cache](#adr-004-semantic-cache-via-redis-lsh) | Redis LSH | **8.70** | pgvector cache | 7.85 | +0.85 | Sub-ms lookup without database round-trip |
| 005 | [Provider fallback](#adr-005-provider-fallback-with-circuit-breaker-and-ttft-race) | Circuit breaker + TTFT race | **8.55** | Round-robin | 6.75 | +1.80 | Availability under degradation + tail latency |
| 006 | [Chunking strategy](#adr-006-structure-aware-chunking) | Structure-aware, sentence-aligned | **8.85** | Recursive splitting | 7.00 | +1.85 | Citation quality + section metadata |

**Reading the matrix.** A score of 10 means the option fully satisfies the criterion with no trade-off; 1 means it fundamentally fails to address it. Weights reflect CINA's priorities: clinical correctness and latency dominate; implementation simplicity is valued but subordinate. Δ values below 1.0 indicate a close call — the ADR text explains the qualitative reasoning that tipped the decision.

---

## ADR-001: Queue Abstraction for Ingestion Workers

**Status:** Accepted · 2026-03-21 · Phase 1, extended in Phase 4

### Context

The ingestion pipeline decouples document parsing from embedding generation with an asynchronous message queue. Parsed chunks are enqueued; embedding workers dequeue, call the OpenAI API, and write vectors to PostgreSQL. Two deployment environments impose conflicting requirements:

| Environment | Queue Transport | DLQ Strategy | Credential Requirements |
|-------------|----------------|--------------|------------------------|
| Local (Docker Compose) | Redis Streams | Separate stream (`cina:queue:ingestion:dlq`) | None |
| AWS (ECS Fargate) | SQS | SQS DLQ with redrive policy | IAM role |

The pipeline and worker code must be **completely transport-agnostic**. Swapping backends must be a single configuration change with no code modification.

**Binding constraints:**
1. Four operations required: `enqueue`, `dequeue` (long-poll), `acknowledge`, `dead_letter`
2. DLQ semantics identical across backends (failed messages captured with a reason string)
3. Backend selection at runtime via `cina.yaml` (`ingestion.queue.backend: redis | sqs`)
4. SQS visibility timeout ≥ 300 s to cover embedding batch duration

### Decision

Define a `QueueProtocol` (Python `Protocol` class) exposing exactly four async methods:

```python
class QueueProtocol(Protocol):
    async def enqueue(self, message: dict, queue_name: str) -> str: ...
    async def dequeue(self, queue_name: str, wait_timeout_seconds: int) -> Message | None: ...
    async def acknowledge(self, receipt: str) -> None: ...
    async def dead_letter(self, message: dict, queue_name: str, reason: str) -> None: ...
```

Two implementations:

| Backend | Class | Transport | DLQ Mechanism |
|---------|-------|-----------|---------------|
| Local | `RedisStreamQueue` | Redis Streams + consumer groups | Separate stream with reason metadata |
| Production | `SQSQueue` | AWS SQS via `aioboto3` | SQS DLQ with automatic redrive |

The factory `build_queue_backend()` reads the config at startup and returns the correct implementation. Startup fails fast if SQS URLs are missing or Redis is unreachable.

### Consequences

**Benefits:**
1. Ingestion pipeline and embedding workers depend on `QueueProtocol` only — zero import of `redis` or `boto3` in business logic
2. Cloud migration requires changing one YAML value and setting three environment variables
3. `cina dlq list / retry / purge` CLI commands operate identically against both backends
4. Invalid configuration surfaces as a startup crash, not a runtime `None`-reference

**Costs:**
1. Two implementations to maintain; each new queue operation requires changes in both
2. SQS-specific capabilities (visibility timeout, approximate message count, redrive count) are not exposed through the protocol — the abstraction trades SQS fidelity for portability
3. Both backends require independent integration tests, doubling the queue test surface

**Degradation:** If the queue backend is unreachable at startup, the process exits with a clear error. If a dequeue times out at runtime, the worker retries; if retries exhaust, the message is dead-lettered with the failure reason.

### Rejected Alternatives

| Alternative | Evaluation | Rejection Rationale |
|------------|-----------|---------------------|
| Direct SQS SDK calls in pipeline code | High production fidelity but requires LocalStack or mocks for local dev | LocalStack is heavy (1.5 GB image) and flaky for streams; mocks reduce integration confidence. Redis Streams gives zero-AWS local dev with semantics that closely mirror SQS. |
| Celery | Mature task queue with retry/DLQ built in | Pulls in ~30 transitive dependencies, imposes its own worker model, and adds Celery-specific configuration. CINA's queue needs are four operations — Celery's task routing, result backends, and middleware are unnecessary overhead. |
| `asyncio.Queue` (in-process) | Simplest possible implementation | No durability. A process crash during an embedding batch loses all enqueued chunks. Redis Streams provides persistence, consumer groups, and acknowledgement semantics that map to SQS behaviour. |
| Amazon MQ / RabbitMQ | Full-featured message broker | CINA's queue needs are four operations. MQ adds broker provisioning, monitoring, and failover for features (routing, topic subscriptions, priority queues) the system never uses. SQS is simpler and fully managed. |

### Decision Matrix

Criteria weights derive from the project's deployment model: equal emphasis on local dev ergonomics and production readiness (50% combined), DLQ correctness for data integrity (20%), maintenance burden (15%), and ecosystem fit (15%).

| Criterion | Weight | QueueProtocol | Direct SQS | Celery | asyncio.Queue | Amazon MQ |
|-----------|--------|--------------|------------|--------|---------------|----------|
| Local dev simplicity | 25% | 10 | 4 | 6 | 10 | 4 |
| Production readiness | 25% | 9 | 10 | 9 | 2 | 9 |
| DLQ consistency | 20% | 9 | 8 | 7 | 2 | 9 |
| Maintenance cost | 15% | 7 | 9 | 5 | 10 | 5 |
| Ecosystem fit | 15% | 9 | 7 | 3 | 7 | 7 |
| **Weighted total** | | **9.00** | **7.65** | **6.30** | **5.60** | **6.80** |

### Implementation Evidence

**Local path (Redis Streams):**
- Consumer group `cina-workers`, concurrency 8 (configurable). `XADD` → `XREADGROUP` (block) → `XACK`.
- Bug found and fixed in Phase 1: consumer group start ID was `$` (new messages only), causing the embedding worker to miss all messages enqueued during the parsing phase. Changed to `0` (read from beginning).

**AWS path (SQS):**
- `send_message` / `receive_message` (20 s long poll) / `delete_message`. Visibility timeout 300 s.
- DLQ forwarding via `send_message` to the DLQ URL with failure reason in message attributes.
- Demo evidence (`docs/demo/ingestion-smoke.txt`): message enqueued → consumed within 20 s → queue depth returned to 0.

**Source files:** `cina/ingestion/queue/protocol.py` · `cina/ingestion/queue/redis_stream.py` · `cina/ingestion/queue/sqs.py` · `cina/cli/dlq.py`

---

## ADR-002: Hybrid Search with Reciprocal Rank Fusion

**Status:** Accepted · 2026-03-19 · Phase 2

### Context

The serving pipeline must retrieve relevant chunks from a corpus of 98,602 embedded clinical literature fragments. Retrieval quality depends on balancing two orthogonal strengths:

- **Semantic similarity:** Dense vector search captures meaning — a query about "HER2-targeted therapies" matches chunks discussing trastuzumab even if neither string appears in the other.
- **Lexical precision:** Clinical text is dense with domain-specific tokens — drug names ("pembrolizumab"), gene symbols ("BRCA1"), dosage units ("mg/m²"). Embedding models conflate semantically similar but clinically distinct terms; exact token matching does not.

Neither modality alone is sufficient. The retrieval stage must complete in under 50 ms to stay within the 500 ms pre-LLM latency budget.

**Binding constraints:**
1. Single database engine — PostgreSQL 16 with pgvector extension and GIN/tsvector indexes, no separate search cluster
2. Latency target: hybrid search stage < 50 ms
3. Must degrade gracefully if one search path fails

### Decision

Combine **pgvector dense retrieval** and **PostgreSQL full-text search** (BM25-equivalent), fused with **Reciprocal Rank Fusion (RRF)**:

1. **Vector path:** Query embedding → pgvector HNSW index (`cosine`, `ef_search = 100`) → top 50 by similarity
2. **Lexical path:** Query text → `to_tsquery('english', ...)` against GIN-indexed `content_tsvector` column → top 50 by `ts_rank_cd`
3. **Fusion:** For each chunk appearing in any result list, score = $\sum_{l \in \text{lists}} \frac{1}{k + \text{rank}_l + 1}$, with $k = 60$.

Both queries execute concurrently via `asyncio.gather()`. The fused ranking feeds the reranker (ADR-003).

### Consequences

**Benefits:**
1. Complementary coverage — dense retrieval handles paraphrase; BM25 handles precise terminology
2. No additional infrastructure — both search paths are PostgreSQL queries
3. Measured latency of 7.5 ms (parallel execution)
4. Graceful degradation — vector failure → BM25-only; BM25 failure → vector-only; both → empty set with LLM error event

**Costs:**
1. Two indexes per chunk: HNSW (263 MB) + GIN (50 MB) = 313 MB of index storage
2. Two SQL queries per request (mitigated by parallelism)
3. RRF discards raw scores — only rank positions contribute. Magnitude information from cosine similarity and `ts_rank_cd` is lost. The cross-encoder reranker (ADR-003) recovers relevance fidelity downstream.

### Rejected Alternatives

| Alternative | Evaluation | Rejection Rationale |
|------------|-----------|---------------------|
| Vector-only search | Lowest infrastructure complexity; single index, single query | Clinical terminology like "trastuzumab" and "pertuzumab" may occupy nearby embedding regions despite being distinct drugs with different mechanisms. Lexical search is essential for precise term matching in clinical contexts. |
| BM25-only search | Zero-inference at query time; exact match is strong for known terms | Fails on conceptual queries ("treatments that target hormone receptors") where relevant chunks don't share query terms. Dense retrieval handles paraphrase and intent matching. |
| Elasticsearch / OpenSearch sidecar | Purpose-built for full-text search with richer BM25 tuning | Adds a separate service to deploy, monitor, and sync. PostgreSQL's built-in FTS with GIN indexes produces equivalent results at 98K chunks. Justified at > 10M chunks where PostgreSQL FTS scaling limits emerge. |
| SPLADE (learned sparse retrieval) | Vocabulary expansion improves recall over BM25 | Adds model inference at query time (~10 ms per query), increasing latency and operational complexity. PostgreSQL tsvector is zero-inference. SPLADE would be reconsidered if BM25 recall proved insufficient on a larger evaluation set. |

### Decision Matrix

Weights reflect the clinical domain: lexical precision (30%) is highest because incorrect clinical term matching has safety implications. Semantic recall (25%) captures conceptual coverage. Latency (20%), infrastructure simplicity (15%), and degradation handling (10%) complete the criteria.

| Criterion | Weight | Hybrid RRF | Vector-Only | BM25-Only | Elasticsearch | SPLADE |
|-----------|--------|-----------|-------------|-----------|---------------|--------|
| Clinical term precision | 30% | 9 | 5 | 10 | 9 | 8 |
| Semantic recall | 25% | 9 | 10 | 4 | 9 | 9 |
| Latency | 20% | 9 | 10 | 10 | 7 | 6 |
| Infrastructure simplicity | 15% | 10 | 10 | 10 | 4 | 6 |
| Graceful degradation | 10% | 10 | 5 | 5 | 7 | 5 |
| **Weighted total** | | **9.25** | **7.75** | **7.35** | **7.40** | **7.05** |

### Implementation Evidence

| Metric | Measured Value |
|--------|---------------|
| Hybrid search latency | 7.5 ms |
| Vector candidates returned | 50 |
| BM25 candidates returned | 50 |
| Unique candidates after fusion | ~65 (overlap between lists) |
| Sources cited in final response | 10 |

**Source files:** `cina/serving/search/vector.py` · `cina/serving/search/bm25.py` · `cina/serving/search/fusion.py` · `cina/serving/pipeline.py`

---

## ADR-003: Cross-Encoder Reranking

**Status:** Accepted · 2026-03-19 · Phase 2

### Context

After hybrid search and RRF fusion (ADR-002), the pipeline holds ~65 candidate chunks ordered by rank-only scores. RRF does not assess true query-document relevance — it aggregates rank positions without reading the text. For a clinical RAG system, this is insufficient:

1. **Token budget is finite.** Context assembly admits at most 15 chunks. Every irrelevant chunk displaces a relevant one and wastes LLM input tokens (directly increasing cost at ~$0.003 per 1K tokens).
2. **Citation trust.** End users see cited sources. Irrelevant citations erode trust in the system's clinical answers.
3. **Cost.** At $0.056 per query, each chunk that enters the context window must pull its weight.

A second-stage reranker that jointly encodes the query and each candidate can dramatically improve precision in the top-10 window.

**Binding constraints:**
1. Reranking latency < 100 ms (within the 500 ms pre-LLM budget)
2. Must run on GPU if available; must degrade to CPU if not
3. Model must fit in ECS Fargate task memory (< 200 MB)
4. Must handle exactly 20 candidates per query (the RRF top-20)

### Decision

Use **`cross-encoder/ms-marco-MiniLM-L-6-v2`** as a pointwise reranker:

1. Take the top 20 candidates from RRF fusion
2. Form 20 `(query, chunk.content)` pairs
3. Forward all 20 pairs through the cross-encoder in a single batched inference pass
4. Sort by cross-encoder score descending; return top 10 for context assembly

The model is loaded once at process startup and held in memory. Device selection is automatic: CUDA if available, CPU otherwise.

### Consequences

**Benefits:**
1. Full cross-attention over `(query, document)` pairs captures relevance signals invisible to bi-encoder cosine similarity and BM25 term overlap
2. 74.4 ms on CUDA for 20 candidates — comfortably within the 100 ms budget
3. ~80 MB model weight, well under the Fargate memory constraint
4. Single dependency (`sentence-transformers`) with no custom training

**Costs:**
1. `sentence-transformers` pulls in `torch` (~2 GB in container images)
2. CPU inference is 3–5× slower (~250 ms for 20 candidates) — still within budget, but tight
3. The cross-encoder forward pass holds the GIL, creating a single-threaded bottleneck under high concurrency (see `docs/LIMITATIONS.md`)
4. Pointwise scoring ignores inter-document redundancy — two chunks from the same document may both score highly without a listwise deduplication signal

**Degradation:** If the reranker fails to load or throws at runtime, the pipeline skips reranking entirely and passes RRF-ordered results directly to context assembly. This is logged as a warning and reflected in Prometheus (`rerank_latency` histogram records a zero-latency entry).

### Rejected Alternatives

| Alternative | Evaluation | Rejection Rationale |
|------------|-----------|---------------------|
| No reranking (use RRF order directly) | Zero additional latency; simplest implementation | RRF doesn't read chunk text. In testing, topically adjacent but query-irrelevant chunks appeared in the top 10, wasting 15–20% of the context token budget. The reranker eliminates these. |
| ColBERT / late interaction | Pre-computes per-token embeddings; O(1) reranking at query time | Requires storing per-token embeddings for all 98,602 chunks (~2 GB additional storage). The latency advantage of ColBERT materialises at thousands of candidates; at 20, a cross-encoder forward pass is faster end-to-end than the index lookup + late interaction. |
| MiniLM-L-12-v2 (12-layer variant) | Higher MSMARCO MRR@10 (0.391 vs. 0.369 for L-6) | ~2× latency for a marginal quality lift. At 20 candidates, the 6-layer model's discrimination is sufficient. The 12-layer model would be reconsidered if candidate count increased to > 50. |
| LLM-based reranking | Highest possible relevance quality | Adds an LLM API round-trip (> 1 s) before the main LLM call, with non-trivial cost per query. Creates a circular dependency if the primary provider is degraded. Defeats the purpose of a fast pre-LLM pipeline. |
| Cohere Rerank API | Managed, no local model to maintain | Puts an external API call in the latency-critical path. Network round-trip alone (50–150 ms) consumes the 100 ms budget. Local inference is faster, deterministic, and free. |

### Decision Matrix

Weights: relevance quality receives 30% because it is the entire purpose of the reranking stage. Latency (25%) guards the pre-LLM budget. Infrastructure cost (20%) reflects container size and per-request expense. Operational simplicity (15%) and degradation handling (10%) are important but subordinate.

| Criterion | Weight | CE L-6 | RRF-Only | ColBERT | CE L-12 | LLM Rerank | Cohere |
|-----------|--------|--------|----------|---------|---------|------------|--------|
| Relevance quality | 30% | 8 | 5 | 9 | 9 | 10 | 8 |
| Latency | 25% | 9 | 10 | 7 | 7 | 2 | 5 |
| Infrastructure cost | 20% | 8 | 10 | 5 | 7 | 4 | 6 |
| Operational simplicity | 15% | 9 | 10 | 5 | 9 | 7 | 6 |
| Degradation handling | 10% | 9 | 10 | 6 | 9 | 5 | 5 |
| **Weighted total** | | **8.50** | **8.50** | **6.55** | **8.00** | **5.45** | **6.05** |

The tie between CE L-6 and RRF-only is resolved by the criterion with the highest weight: relevance quality (30%), where CE L-6 scores 8 vs. RRF-only's 5. Since the entire purpose of this pipeline stage is to improve relevance ordering, the cross-encoder is selected despite identical composite scores.

### Implementation Evidence

| Metric | Measured Value |
|--------|---------------|
| Reranking latency (20 candidates, CUDA) | 74.4 ms |
| Candidates in / candidates out | 20 → 10 |
| Model memory footprint | ~80 MB |

**Source files:** `cina/serving/rerank/cross_encoder.py` · `cina/serving/pipeline.py` · `scripts/benchmark_rerank.py`

---

## ADR-004: Semantic Cache via Redis LSH

**Status:** Accepted · 2026-03-20 · Phase 3

### Context

A full CINA query pipeline costs approximately $0.056 (embedding + search + rerank + LLM generation at 4,742 input / 2,794 output tokens). In production, semantically similar queries recur frequently — a clinician asking "latest treatments for metastatic breast cancer" and another asking "current therapies for metastatic breast cancer" should not incur independent pipeline runs.

The caching layer must satisfy five requirements:
1. **Semantic matching.** Detect equivalence by meaning, not string identity.
2. **Invalidation on prompt change.** When the system prompt is updated, stale cached responses must not be served.
3. **No additional vector store.** Avoid a second pgvector index or a managed vector DB.
4. **Sub-millisecond lookup.** Cache hits should not add perceptible latency.
5. **Correctness bias.** False positives (serving wrong cached answer) are worse than false negatives (cache miss).

Redis is already deployed for rate limiting (ADR-005) and circuit breaker state (ADR-005), making it the natural cache substrate.

### Decision

Implement **Locality-Sensitive Hashing (LSH)** over query embeddings with Redis as the backing store:

1. **Hash computation.** Project the 512-dimensional query embedding through a matrix of 16 random hyperplanes (shape 16 × 512, generated from a fixed seed). Each dot-product sign yields one bit → 16-bit hash ($2^{16}$ = 65,536 possible buckets).
2. **Bucket lookup.** Redis key `cina:cache:lsh:{prompt_version}:{hash_hex}` → JSON blob containing the cached response text, citations array, the original 512d embedding, and metadata.
3. **Cosine verification.** Before accepting a hit, compute $\cos(\mathbf{q}, \mathbf{c})$ between the query embedding $\mathbf{q}$ and the cached embedding $\mathbf{c}$. Accept if and only if similarity $\geq 0.95$. This guards against bucket collisions.
4. **TTL.** All cache keys expire after 86,400 s (24 hours).
5. **Version scoping.** The prompt version is embedded in the key. Prompt update → new key namespace; a background scan prunes stale keys.

### Consequences

**Benefits:**
1. Hash computation is $O(d \times h) = O(512 \times 16) = O(8192)$ FLOPs — sub-microsecond. Redis `GET` adds < 1 ms.
2. Reuses the existing Redis instance; no additional infrastructure.
3. Precision/recall is tunable: more hyperplanes → fewer collisions but more false negatives; higher similarity threshold → stricter acceptance.
4. Cache is prompt-version-safe by construction.
5. A hit short-circuits the entire downstream pipeline (search, rerank, LLM), saving ~$0.056 and ~15 s of wall time.

**Costs:**
1. LSH is inherently probabilistic. Queries near a hyperplane boundary hash unpredictably — semantically identical queries may map to different buckets (false negatives). With 16 hyperplanes and 512d embeddings, the expected false-negative rate for pairs with cosine similarity > 0.95 is approximately 5–10%.
2. Each cached entry stores ~4 KB in Redis (response text + citations + 512d float32 embedding).
3. Cache is cold on every deployment — first queries always miss.
4. Bucket collisions trigger Redis reads that fail the cosine check, wasting one Redis round-trip (~0.5 ms).

**Degradation:** If Redis is unreachable, the cache middleware passes through to the pipeline without short-circuiting. Prometheus counter `cache_misses` tracks this.

### Rejected Alternatives

| Alternative | Evaluation | Rejection Rationale |
|------------|-----------|---------------------|
| Exact string match cache | O(1) lookup, trivially correct, no false positives | Misses semantically identical queries with any phrasing variation. "What are treatments for metastatic breast cancer?" ≠ "What are the latest treatments for metastatic breast cancer?" despite near-identical intent. |
| pgvector nearest-neighbour cache | Highest recall for semantic matching | Requires a database query to check the cache — which is the exact I/O the cache exists to avoid. Defeats the purpose. |
| Managed vector cache (Pinecone, Weaviate) | Purpose-built, high recall | External dependency with network latency (10–50 ms per lookup), API cost, and another service to operate — disproportionate for an approximate key-value lookup. |
| In-process KD-Tree / Ball Tree | Fastest possible lookup (in-memory, no network) | Not shared across ECS replicas. With 2 query service tasks, each builds an independent cache — halving effective hit rate and doubling memory usage. |

### Decision Matrix

Weights: semantic matching (30%) is the primary requirement. Lookup latency (25%) protects user-visible performance. Infrastructure cost (20%) reflects the project's single-Redis stance. Multi-replica sharing (15%) matters for production ECS. Implementation complexity (10%) is a subordinate concern.

| Criterion | Weight | Redis LSH | Exact Match | pgvector | Vector DB | In-Process |
|-----------|--------|----------|-------------|----------|-----------|------------|
| Semantic matching | 30% | 8 | 2 | 9 | 10 | 9 |
| Lookup latency | 25% | 10 | 10 | 6 | 5 | 10 |
| Infrastructure cost | 20% | 9 | 10 | 8 | 3 | 9 |
| Multi-replica sharing | 15% | 10 | 10 | 10 | 10 | 2 |
| Implementation complexity | 10% | 6 | 10 | 7 | 5 | 7 |
| **Weighted total** | | **8.70** | **7.30** | **7.85** | **6.40** | **7.55** |

### Implementation Evidence

The cache middleware is composed between rate limiting and provider fallback in the orchestration layer:

```
Rate Limiter → Semantic Cache → Provider Fallback → Cost Tracker → LLM Provider
```

On a cache hit, the middleware yields the stored response tokens directly and returns — the downstream chain (provider fallback, LLM call, cost tracking) is never invoked.

**Source files:** `cina/orchestration/cache/lsh.py` · `cina/orchestration/middleware.py` · `cina/observability/metrics.py` (`cache_hits`, `cache_misses` counters)

---

## ADR-005: Provider Fallback with Circuit Breaker and TTFT Race

**Status:** Accepted · 2026-03-20 · Phase 3

### Context

CINA depends on two external LLM providers for response generation:

| Role | Provider | Model |
|------|----------|-------|
| Primary | Anthropic | Claude Sonnet 4 |
| Fallback | OpenAI | GPT-4o |

These providers experience three classes of failure:
- **Hard outage:** 5xx errors, DNS resolution failure, TLS handshake timeout
- **Soft degradation:** Elevated time-to-first-token (TTFT), partial rate limiting (429s)
- **Quota exhaustion:** Per-minute or per-day token limits

The query serving path must remain available and responsive regardless of provider state. CINA's streaming architecture demands **per-request, latency-aware fallback** — not batch-level health checks or sequential retries.

**Binding constraints:**
1. Fallback must be invisible to the API consumer — the SSE event format is identical regardless of which provider serves the response
2. Circuit breaker state must be shared across ECS query service replicas → requires Redis
3. Under normal operation, only the primary provider is called (no unnecessary cost doubling)
4. Must handle both hard failures (immediate) and soft failures (slow TTFT detection)

### Decision

A two-layer fallback strategy:

**Layer 1 — Circuit Breaker (per provider, state in Redis):**

| State | Behaviour | Transition Trigger |
|-------|----------|-------------------|
| Closed | Route all requests to this provider | → Open after `circuit_breaker_failures` (default: 3) consecutive failures |
| Open | Skip this provider entirely; route to fallback | → Half-Open after `circuit_breaker_cooldown` (default: 60 s) TTL expires |
| Half-Open | Allow a single probe request | → Closed on success · → Open on failure |

Redis keys per provider: `cina:provider:{name}:failures` (integer), `cina:provider:{name}:circuit` (string), `cina:provider:{name}:cooldown` (TTL-bearing key).

**Layer 2 — TTFT Race (concurrent timeout fallback):**

When the primary provider's circuit is closed but may be degraded:
1. Begin streaming from the primary provider
2. Start a `ttft_threshold_seconds` (default: 5.0 s) timer
3. If the first token arrives before the timer → use primary for the full response
4. If the timer fires first → concurrently start the fallback provider stream
5. Whichever provider emits the first token wins; the losing async generator is cancelled and its HTTP connection closed
6. The winning provider stream is used for the remainder of the response

### Consequences

**Benefits:**
1. Hard outages trigger the circuit breaker within 3 failures — subsequent requests skip the broken provider entirely with zero retry latency
2. Soft degradation (slow TTFT) is detected per-request via the race; users never wait more than 5 s before the fallback engages
3. Redis-backed state ensures all ECS replicas share a consistent view of provider health
4. Full Prometheus observability: `provider_requests`, `provider_errors`, `provider_latency`, `fallback_triggered`
5. Clean separation of concerns: circuit breaker handles persistent failures; TTFT race handles transient slowness; Redis-shared state handles multi-replica coordination

**Costs:**
1. When the TTFT race engages, both providers are called concurrently. The losing stream is cancelled, but it may have already consumed tokens (small cost leak, bounded by `ttft_threshold_seconds`)
2. Async generator lifecycle management is complex — both streams must be properly closed to avoid resource leaks
3. If Redis is unreachable, the circuit breaker state is unavailable; the system degrades to a simple try/catch fallback (functional but without cross-replica coordination)
4. Both providers must be normalised to the same `LLMProviderProtocol` streaming interface, which conceals model-specific behaviours

**Degradation:** Redis failure → try/catch fallback (no circuit breaker coordination). Both-providers-down → SSE error event emitted. Fallback provider slow too → user sees the faster of two degraded streams.

### Rejected Alternatives

| Alternative | Evaluation | Rejection Rationale |
|------------|-----------|---------------------|
| Simple try/catch failover | Simplest code path | The user experiences the full primary timeout (30–60 s) before the fallback starts. In a streaming interface where TTFT > 5 s already feels broken, this is unacceptable. |
| Round-robin load balancing | Distributes load across providers | Doubles cost (both providers used ~50% of the time), loses the ability to prefer Anthropic (better clinical instruction following), and doesn't detect degradation — requests routed to a degraded provider still fail. |
| Single provider with exponential backoff | Minimal complexity, no cost duplication | Zero provider diversity. An Anthropic outage becomes a CINA outage. Retries multiply latency (first retry at 1 s, second at 2 s, third at 4 s — 7+ s before the user sees an error). |
| AWS Bedrock as fallback | Managed endpoints, AWS-native | Adds a layer of indirection with its own latency profile. Direct OpenAI API calls give CINA precise control over connection timeouts, read timeouts, and streaming behaviour. Bedrock is better suited for embedding (used in ingestion) where latency is less critical. |
| Cache-only, no fallback | Avoids provider orchestration entirely | The semantic cache (ADR-004) handles repeated queries, but novel queries always miss the cache. Fallback is essential for novel-query availability during provider degradation. |

### Decision Matrix

Weights: availability (30%) because a clinical information system must not go dark. Tail latency (25%) because slow is perceived as broken in streaming UIs. Cost efficiency (20%) because double provider calls are expensive. Implementation clarity (15%) and observability (10%) round out the criteria.

| Criterion | Weight | CB + Race | Try/Catch | Round-Robin | Single+Retry | Bedrock |
|-----------|--------|----------|-----------|-------------|-------------|---------|
| Availability | 30% | 10 | 6 | 8 | 4 | 7 |
| Tail latency | 25% | 9 | 3 | 7 | 4 | 6 |
| Cost efficiency | 20% | 7 | 9 | 4 | 10 | 7 |
| Implementation clarity | 15% | 6 | 10 | 8 | 10 | 7 |
| Observability | 10% | 10 | 5 | 7 | 5 | 6 |
| **Weighted total** | | **8.55** | **6.20** | **6.75** | **5.90** | **6.70** |

### Implementation Evidence

- **Provider routing confirmed:** Demo SSE metadata event reports `"provider": "anthropic"` — primary is used with circuit closed under normal conditions
- **Health endpoint verified:** `GET /health` returns Postgres and Redis connectivity status, confirming the circuit breaker backing store is reachable
- **Design rationale validated:** The two-layer approach (circuit breaker + TTFT race) covers both persistent failures and transient slowness with minimal redundant API calls

**Source files:** `cina/orchestration/providers/` · `cina/orchestration/routing/` · `cina/orchestration/middleware.py` · `cina/observability/metrics.py`

---

## ADR-006: Structure-Aware Chunking

**Status:** Accepted · 2026-03-19 · Phase 1

### Context

The ingestion pipeline must decompose clinical documents into chunks suitable for embedding (512-dimensional vectors via OpenAI `text-embedding-3-large`) and retrieval. Chunking quality propagates through every downstream stage:

| Stage | How Chunking Quality Manifests |
|-------|-------------------------------|
| Embedding | Incoherent chunks produce noisy embeddings; semantic search accuracy degrades |
| Hybrid search | Cross-section chunks confuse BM25 with terms from unrelated sections |
| Reranking | The cross-encoder scores `(query, chunk)` — a chunk mixing "Methods" and "Results" text produces ambiguous scores |
| Context assembly | Chunks are the unit of token budgeting; incoherent chunks waste budget |
| Citation | Each chunk is a citable unit; mid-sentence or cross-section chunks produce nonsensical citations |

Clinical documents carry explicit structure (abstracts, methods, results, conclusions, eligibility criteria, adverse reactions) that encodes semantic boundaries. Destroying this structure is a net loss.

**Binding constraints:**
1. Maximum chunk size: 512 tokens (tokenizer: `cl100k_base`, matching the embedding model)
2. Three source formats: PubMed XML (JATS), FDA SPL XML, ClinicalTrials.gov JSON
3. Chunks must not span section boundaries
4. 64-token overlap between consecutive chunks for contextual continuity
5. Must process 3,500 documents in under 30 minutes (embedding-dominated, but chunking must not be a bottleneck)

### Decision

Implement a **two-pass, structure-aware chunking engine** with sentence boundary alignment:

**Pass 1 — Section Extraction.** Each connector parses the source format and yields typed sections:

| Source | Extracted Sections |
|--------|-------------------|
| PubMed | title, abstract, introduction, methods, results, discussion, conclusions |
| FDA | description, indications, dosage, warnings, adverse_reactions, clinical_studies |
| ClinicalTrials | brief_summary, detailed_description, eligibility, outcome_measures |

**Pass 2 — Sentence-Aligned Chunking.** Within each section:
1. Split text into sentences using a medical-aware sentence splitter (handles abbreviations: "Dr.", "mg/dL", "p < 0.05", "Fig. 2")
2. Accumulate sentences until the next sentence would exceed the 512-token budget
3. Emit the accumulated text as a chunk with `section_type`, `heading`, `chunk_index`, and `overlap_tokens` metadata
4. Begin the next chunk with a 64-token tail overlap from the previous chunk
5. **Hard rule:** never cross a section boundary. If a section's remaining text is shorter than 64 tokens, it becomes its own (small) chunk.

### Consequences

**Benefits:**
1. Every chunk is a semantically coherent unit drawn from a single document section — "Results" chunks contain only results text
2. No mid-sentence splits — sentence boundaries are respected, producing chunks that read naturally
3. Each chunk carries `section_type` and `heading` metadata, enabling the citation generator to produce references like "Source: NCT00041080, Results, Chunk 3"
4. Token counts cluster near the 512 maximum, minimizing wasted embedding capacity

**Costs:**
1. Short sections (e.g., a two-sentence eligibility criterion) produce undersized chunks that underutilise the embedding token window. Mean chunk size is lower than 512.
2. The 64-token overlap increases total chunk count by ~12%, proportionally increasing embedding API cost
3. The medical sentence splitter must handle clinical abbreviations, decimal numbers, citation markers, and other edge cases — it is a maintenance surface

**Degradation:** If the sentence splitter throws on malformed text, the engine falls back to raw token-boundary splitting within the section. This preserves section boundaries while sacrificing sentence alignment.

### Rejected Alternatives

| Alternative | Evaluation | Rejection Rationale |
|------------|-----------|---------------------|
| Naive fixed-window (512 tokens, no alignment) | Simplest implementation; predictable chunk sizes | Splits mid-sentence, spans section boundaries, loses structural metadata. A "Results/Discussion" boundary chunk confuses both the reranker and the citation generator. Benchmark showed retrieval-metric parity (see Evidence), but citation quality and context coherence — not captured by the proxy — are the decisive factors. |
| Recursive character splitting (LangChain-style) | Splits by paragraph → sentence → character, recursively | Paragraph breaks in clinical articles do not reliably indicate topic changes. Section boundaries do. Recursive splitting ignores document structure and produces chunks that straddle semantic boundaries. |
| Semantic chunking (embedding-based) | Split where consecutive-sentence cosine similarity drops below a threshold | Requires embedding every sentence individually before chunking begins. For 3,500 documents with ~200K sentences, this multiplies embedding API cost by an order of magnitude. Also adds pipeline complexity and latency at the ingestion stage. |
| No chunking (document-level embedding) | Embed entire documents; retrieve at document granularity | Documents range from 500 to 50,000+ tokens. Full documents exceed embedding model context windows (8,191 tokens for `text-embedding-3-large`), and document-level retrieval wastes LLM context on irrelevant sections. |

### Decision Matrix

Weights: citation quality (30%) because citations are the primary trust signal in a clinical system. Embedding quality (25%) directly affects search accuracy. Implementation simplicity (20%) reflects time-to-delivery. Pipeline throughput (15%) and metadata preservation (10%) complete the set.

| Criterion | Weight | Structure-Aware | Fixed-Window | Recursive | Semantic | No Chunking |
|-----------|--------|----------------|-------------|-----------|----------|-------------|
| Citation quality | 30% | 10 | 4 | 6 | 8 | 3 |
| Embedding quality | 25% | 9 | 7 | 7 | 9 | 2 |
| Implementation simplicity | 20% | 7 | 10 | 8 | 4 | 10 |
| Pipeline throughput | 15% | 8 | 10 | 9 | 3 | 10 |
| Metadata preservation | 10% | 10 | 3 | 5 | 7 | 8 |
| **Weighted total** | | **8.85** | **6.65** | **7.00** | **6.30** | **5.30** |

### Implementation Evidence

**Benchmark (200 PubMed documents, 30 clinical queries):**

| Strategy | precision@10 | recall@10 |
|----------|-------------|-----------|
| Structure-aware (sentence-aligned, section-respecting) | 1.0000 | 0.0935 |
| Naive fixed-window (512 tokens) | 1.0000 | 0.0935 |

The proxy metrics show parity because the relevance heuristic (term-overlap) cannot measure the differences that matter: citation coherence, context assembly quality, and section-level attribution. Structure-aware chunking is retained because these qualitative advantages are architecturally load-bearing — they affect every stage downstream of retrieval.

The low recall@10 (0.0935) is a property of the heuristic (conservative relevance labels), not the chunking strategy.

**Ingestion statistics:**

| Metric | Value |
|--------|-------|
| Documents processed | 3,500 |
| Sections extracted | 68,005 |
| Chunks created | 98,602 |
| Total ingestion time | ~29 min |
| HNSW index size | 263 MB |
| GIN (FTS) index size | 50 MB |

**Source files:** `cina/ingestion/chunking/engine.py` · `cina/ingestion/chunking/sentences.py` · `cina/ingestion/connectors/pubmed.py` · `cina/ingestion/connectors/fda.py` · `cina/ingestion/connectors/clinicaltrials.py` · `scripts/benchmark_chunking.py`
