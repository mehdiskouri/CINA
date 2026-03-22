# ADR-004: Semantic Cache via Redis LSH

## Status

Accepted

## Date

2026-03-20

## Context

CINA queries are expensive: each one triggers embedding generation, hybrid search, cross-encoder reranking, and LLM generation (≈ $0.056/query at current token counts). In production, semantically similar queries are common — users asking slight variations of the same clinical question should not incur full pipeline cost each time.

Phase 3 requires a caching layer that can identify **semantically equivalent** queries (not just exact string matches) and return cached responses. The cache must:

- Detect semantic similarity, not just string equality
- Support cache invalidation when the system prompt changes
- Avoid introducing a second vector index that duplicates storage costs
- Have sub-millisecond lookup latency for cache hits
- Be configurable (threshold, TTL, enable/disable)

### Constraints

- Redis is already deployed for rate limiting and circuit breaker state
- No additional persistent vector store budget
- Cache must be versioned by prompt version (prompt changes invalidate prior entries)
- False positives (returning wrong cached answer) are worse than false negatives (cache miss)

## Decision

Implement a **Locality-Sensitive Hashing (LSH)** based semantic cache in Redis:

1. **Hash computation:** Project the query embedding through 16 random hyperplanes (16 × 512 matrix, generated once from a fixed seed). Each dot product yields a sign bit → 16-bit hash.
2. **Bucket lookup:** Key format `cina:cache:lsh:{hash_hex}` → JSON blob containing cached response, citations, embedding, and metadata.
3. **Verification:** Before accepting a hit, compute cosine similarity between the query embedding and the cached embedding. Accept only if similarity ≥ 0.95.
4. **Version scoping:** Keys include the prompt version. When the prompt version changes, a background scan invalidates stale entries.
5. **TTL eviction:** All cache keys have a 24-hour TTL.

### Implementation

- `cina/orchestration/cache/lsh.py` — LSH hasher and cache client
- `cina/orchestration/middleware.py` — Cache middleware in the composition chain
- Hyperplane matrix persisted in Redis on first generation, reused thereafter

### Configuration

```yaml
orchestration:
  cache:
    enabled: true
    num_hyperplanes: 16
    similarity_threshold: 0.95
    ttl_seconds: 86400
```

## Consequences

### Benefits

- **Fast lookups:** LSH hash computation is O(d × h) where d=512 and h=16 — microseconds. Redis key lookup is sub-millisecond.
- **No additional vector store:** Reuses Redis, avoids a second pgvector index or a dedicated vector cache
- **Tunable precision/recall tradeoff:** Adjusting `num_hyperplanes` and `similarity_threshold` controls the false-positive rate
- **Prompt-version-safe:** Cache automatically segments by prompt version; stale entries don't persist
- **Cost savings:** Cache hits skip embedding, search, rerank, and LLM generation — saving ~$0.056/hit

### Costs

- **False negatives:** LSH is probabilistic — semantically identical queries may hash to different buckets if they're near a hyperplane boundary. The 0.95 threshold also rejects marginally similar queries.
- **Storage overhead:** Each cached entry stores the full response text, citations array, and the 512d embedding (~4 KB per entry in Redis)
- **Cold start:** Cache is empty on deployment; first queries always miss
- **Bucket collisions:** Multiple queries may map to the same bucket but fail the cosine check, causing unnecessary Redis reads

## Rejected Alternatives

### 1. Exact String Match Cache

Cache keyed by normalized query string.

**Rejected because:** "What are treatments for metastatic breast cancer?" and "What are the latest treatments for metastatic breast cancer?" are semantically identical but would miss an exact-match cache. Clinical queries routinely have minor phrasing variations.

### 2. pgvector Nearest-Neighbor Cache

Store cached responses in a PostgreSQL table with a vector column and use pgvector `<=>` operator for similarity lookup.

**Rejected because:** Adds another pgvector query in the hot path. The entire point of caching is to **skip** the database. Redis is already in-memory and sub-millisecond.

### 3. Dedicated Vector Cache (Pinecone / Weaviate)

Use a managed vector database as a query cache layer.

**Rejected because:** Adds external dependency, network latency, and cost for what amounts to a simple key-value lookup with approximate matching. LSH + Redis achieves the same goal with existing infrastructure.

### 4. KD-Tree / Ball Tree In-Process

Store cached embeddings in an in-process spatial index.

**Rejected because:** Not shared across multiple ECS tasks. In production, the query service runs 2 replicas — an in-process cache would have independent hit rates and waste memory. Redis is shared.

## Decision Matrix

| Criteria (weight) | Redis LSH | Exact Match | pgvector Cache | Vector DB | In-Process |
|---|---|---|---|---|---|
| Semantic matching (30%) | 8 | 2 | 9 | 10 | 9 |
| Lookup latency (25%) | 10 | 10 | 6 | 5 | 10 |
| Infrastructure cost (20%) | 9 | 10 | 8 | 3 | 9 |
| Multi-replica sharing (15%) | 10 | 10 | 10 | 10 | 2 |
| Implementation complexity (10%) | 6 | 10 | 7 | 5 | 7 |
| **Weighted score** | **8.70** | **7.30** | **7.85** | **6.40** | **7.55** |

## Implementation Evidence

### Architecture

```
Query Embedding (512d)
  → LSH: 16 hyperplane dot products → 16-bit hash
    → Redis GET cina:cache:lsh:{hash}
      → Hit? Verify cosine ≥ 0.95
        → Accept: stream cached response
        → Reject: proceed to search pipeline
```

### Cache Middleware Position

The semantic cache is composed as middleware in the orchestration layer, positioned **after** rate limiting and **before** provider fallback:

```
Rate Limiter → Semantic Cache → Provider Fallback → Cost Tracker → LLM Provider
```

On cache hit, the middleware short-circuits the entire downstream chain.

### File References

- [`cina/orchestration/cache/lsh.py`](../../cina/orchestration/cache/lsh.py) — LSH hasher, cache get/set, cosine verification
- [`cina/orchestration/middleware.py`](../../cina/orchestration/middleware.py) — Middleware composition chain
- [`cina/config/schema.py`](../../cina/config/schema.py) — `CacheConfigModel` with hyperplanes, threshold, TTL
- [`cina/observability/metrics.py`](../../cina/observability/metrics.py) — `cache_hits` and `cache_misses` Prometheus counters
