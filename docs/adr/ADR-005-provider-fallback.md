# ADR-005: Provider Fallback with Circuit Breaker and TTFT Race

## Status
Accepted

## Context
CINA must remain available during provider degradation and reduce user-facing latency when a primary provider is slow to first token.

## Decision
Adopt a two-layer fallback strategy:
- Circuit breaker per provider in Redis (`closed -> open -> half-open`)
- Concurrent timeout fallback race for TTFT

### Circuit Breaker
- Failure counter key: `cina:provider:{name}:failures`
- State key: `cina:provider:{name}:circuit`
- Cooldown key: `cina:provider:{name}:cooldown`
- Open circuit after configured failures
- Transition to half-open after cooldown TTL expires

### TTFT Race
- Start primary stream
- If TTFT exceeds threshold (default 5s), start fallback stream concurrently
- Whichever stream emits first token wins
- Cancel and close losing stream

## Consequences
- Better resilience and lower tail latency
- More complex orchestration flow and observability requirements
- Slight increase in duplicated provider calls when races trigger
