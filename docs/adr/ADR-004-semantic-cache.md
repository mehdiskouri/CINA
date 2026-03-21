# ADR-004: Semantic Cache via Redis LSH

## Status
Accepted

## Context
Phase 3 requires low-latency reuse for semantically similar queries without introducing a second vector index that duplicates storage and operational cost.

## Decision
Use Redis-backed semantic cache with:
- LSH bucketing using random hyperplanes (16 x 512)
- Per-bucket candidate payloads in Redis JSON blobs
- Cosine similarity verification before hit acceptance
- Version-scoped keys (`cina:cache:{prompt_version}:{hash}`)
- TTL-based eviction and prompt-version invalidation scan

## Consequences
- Fast lookups and no additional persistent vector store
- Slightly probabilistic bucket collisions, mitigated by cosine threshold verification
- Cache quality depends on embedding stability and configured threshold
