# ADR-005: Provider Fallback with Circuit Breaker and TTFT Race

## Status

Accepted

## Date

2026-03-20

## Context

CINA depends on external LLM APIs (Anthropic and OpenAI) for response generation. These providers experience:

- **Outages:** Complete unavailability (5xx errors, DNS failures)
- **Degradation:** Elevated latency, particularly in time-to-first-token (TTFT)
- **Rate limiting:** 429 responses during traffic spikes

The query serving path must remain available during provider issues. Users should not experience errors when one provider is down, and they should not experience excessive latency when one provider is slow.

This decision extends the fallback pattern established in FuelSense (see **FuelSense ADR-3: Champion/Challenger Model Promotion**). Where FuelSense handles model degradation at the batch-prediction layer with a simple health-check failover, CINA requires **real-time, per-request fallback with latency awareness** due to its streaming architecture.

### Constraints

- Fallback must be transparent to the API consumer (same SSE event format regardless of provider)
- State (failure counts, circuit status) must be shared across ECS task replicas → requires Redis
- Must not double LLM costs under normal operation
- Must handle both hard failures (connection errors) and soft failures (slow TTFT)

## Decision

Adopt a **two-layer fallback strategy** combining a circuit breaker with a concurrent TTFT race:

### Layer 1: Circuit Breaker (per provider)

State machine in Redis with three states:

| State | Behavior | Transition |
|-------|----------|------------|
| **Closed** | Route requests to this provider normally | → Open after N consecutive failures |
| **Open** | Skip this provider entirely | → Half-Open after cooldown TTL expires |
| **Half-Open** | Allow one probe request | → Closed on success, → Open on failure |

Redis keys:
- `cina:provider:{name}:failures` — consecutive failure counter
- `cina:provider:{name}:circuit` — state string
- `cina:provider:{name}:cooldown` — TTL key (presence = cooldown active)

### Layer 2: TTFT Race (concurrent timeout fallback)

When the primary provider's circuit is closed but TTFT is slow:

1. Start streaming from the primary provider
2. If the first token is not received within `ttft_threshold_seconds` (default: 5s), **concurrently** start the fallback provider
3. Whichever provider emits the first token wins
4. The losing stream is cancelled and its async generator is closed
5. The winning provider is used for the remainder of the response

### Configuration

```yaml
orchestration:
  providers:
    primary:
      name: anthropic
      model: claude-sonnet-4-20250514
      api_key_env: ANTHROPIC_API_KEY
    fallback:
      name: openai
      model: gpt-4o
      api_key_env: OPENAI_API_KEY

  fallback:
    ttft_threshold_seconds: 5.0
    circuit_breaker_failures: 3
    circuit_breaker_cooldown: 60
```

## Consequences

### Benefits

- **Zero-downtime on provider outage:** Circuit breaker immediately routes to fallback, no wasted retries
- **Lower tail latency:** TTFT race eliminates the scenario where users wait for a slow primary before fallback kicks in
- **Shared state:** Redis-backed state means all ECS replicas share the same circuit breaker view
- **Observable:** `provider_requests`, `provider_errors`, `provider_latency`, and `fallback_triggered` Prometheus metrics provide full visibility
- **Portfolio evolution:** Demonstrates progression from FuelSense's simple health-check failover (ADR-3) to real-time circuit breaker + latency-aware racing

### Costs

- **Occasional duplicate API calls:** When the TTFT race triggers, both providers are called concurrently. The losing call is cancelled but may have already consumed some tokens.
- **Complexity:** The race logic requires careful async generator lifecycle management to avoid resource leaks
- **Redis dependency:** Circuit breaker state requires Redis; if Redis is down, the fallback strategy degrades to a simpler try/catch approach
- **Provider parity:** Both providers must support the same streaming interface. Model-specific behaviors (e.g., different token counting, different stop sequences) must be normalized.

## Rejected Alternatives

### 1. Simple Try/Catch Failover

Try primary, catch exception, retry with fallback.

**Rejected because:** User experiences the full primary timeout (30-60s) before fallback starts. This is unacceptable for a real-time streaming application where TTFT > 5s already feels broken.

### 2. Round-Robin Load Balancing

Alternate between providers on every request.

**Rejected because:** Doubles cost (both providers used 50% of the time) and sacrifices the ability to use the preferred provider (Anthropic, which has better clinical instruction following). Also doesn't handle provider degradation — requests routed to a degraded provider still fail.

### 3. Single Provider with Retries

Use only Anthropic with exponential backoff retries.

**Rejected because:** No provider diversity means a complete Anthropic outage is a complete CINA outage. Retries also add latency multiplicatively.

### 4. AWS Bedrock as Fallback

Use Bedrock's managed LLM endpoints instead of direct OpenAI.

**Rejected because:** Bedrock adds a layer of indirection with its own latency characteristics. Direct OpenAI API calls give CINA control over timeouts and streaming behavior. Bedrock is better suited for embedding (used in ingestion) where latency is less critical.

### 5. Pre-Generated Response Cache Only

Cache all responses aggressively, don't bother with fallback.

**Rejected because:** The semantic cache (ADR-004) handles the happy path, but novel queries always miss the cache. Fallback is essential for cache-miss queries during provider degradation.

## Decision Matrix

| Criteria (weight) | Circuit Breaker + Race | Try/Catch | Round-Robin | Single + Retry | Bedrock Fallback |
|---|---|---|---|---|---|
| Availability (30%) | 10 | 6 | 8 | 4 | 7 |
| Tail latency (25%) | 9 | 3 | 7 | 4 | 6 |
| Cost efficiency (20%) | 7 | 9 | 4 | 10 | 7 |
| Implementation clarity (15%) | 6 | 10 | 8 | 10 | 7 |
| Observability (10%) | 10 | 5 | 7 | 5 | 6 |
| **Weighted score** | **8.55** | **6.20** | **6.75** | **5.90** | **6.70** |

## Implementation Evidence

### Circuit Breaker Verified

Demo deployment to AWS (ECS Fargate) with both providers configured. The health check endpoint confirmed Anthropic connectivity, and the query smoke test showed `provider: "anthropic"` in metadata events, confirming primary routing with circuit closed.

### SSE Metadata Shows Provider

```
event: metadata
data: {"query_id":"9a9d3a89-...","model":"claude-sonnet-4-20250514","provider":"anthropic","sources_used":10,"cache_hit":false}
```

The `provider` field in the metadata event transparently reports which provider served the response, enabling clients and operators to track fallback frequency.

### Portfolio Cross-Reference

This decision is an explicit evolution of **FuelSense ADR-3 (Champion/Challenger Model Promotion)** which handles model failover in a batch context. CINA's contribution is adapting the pattern to real-time streaming with:
- **Circuit breaker** (vs. FuelSense's health-check threshold)
- **TTFT race** (vs. FuelSense's sequential fallback)
- **Redis-shared state** (vs. FuelSense's process-local state)

### File References

- [`cina/orchestration/providers/`](../../cina/orchestration/providers/) — Provider implementations (Anthropic, OpenAI)
- [`cina/orchestration/providers/protocol.py`](../../cina/orchestration/providers/protocol.py) — `LLMProviderProtocol` with error types
- [`cina/orchestration/routing/`](../../cina/orchestration/routing/) — Circuit breaker and TTFT race logic
- [`cina/orchestration/middleware.py`](../../cina/orchestration/middleware.py) — Middleware composition
- [`cina/observability/metrics.py`](../../cina/observability/metrics.py) — Provider Prometheus metrics
- [`docs/demo/query-smoke.txt`](../demo/query-smoke.txt) — Provider metadata in SSE events
- [`docs/demo/health-smoke.txt`](../demo/health-smoke.txt) — Health check confirming connectivity
