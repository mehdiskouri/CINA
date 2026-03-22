# ADR-001: Queue Abstraction for Ingestion Workers

## Status

Accepted

## Date

2026-03-21

## Context

CINA's ingestion pipeline decouples document parsing from embedding generation using a message queue. Parsed chunks are enqueued; embedding workers dequeue, call the OpenAI API, and write vectors to PostgreSQL. This architecture must work in two distinct environments:

1. **Local development:** Docker Compose with Redis — no AWS credentials, fast iteration
2. **AWS production:** ECS Fargate with SQS + DLQ — managed, durable, with dead-letter redrive

The embedding workers and ingestion pipeline orchestrator should contain **zero queue-specific code**. Swapping queue backends should be a configuration change, not a code change.

### Constraints

- Both backends must support: enqueue, dequeue (with long-poll), acknowledge, and dead-letter
- DLQ semantics must be consistent (failed messages are captured with a reason string)
- Backend selection must be runtime-configurable without code changes
- SQS visibility timeout must be long enough for embedding batches (~300s)

## Decision

Define a `QueueProtocol` (Python `Protocol` class) with four operations and provide two implementations:

```python
class QueueProtocol(Protocol):
    async def enqueue(self, message: dict, queue_name: str) -> str: ...
    async def dequeue(self, queue_name: str, wait_timeout_seconds: int) -> Message | None: ...
    async def acknowledge(self, receipt: str) -> None: ...
    async def dead_letter(self, message: dict, queue_name: str, reason: str) -> None: ...
```

**Implementations:**

| Backend | Class | Transport | DLQ Mechanism |
|---------|-------|-----------|---------------|
| Local | `RedisStreamQueue` | Redis Streams + consumer groups | Separate Redis Stream (`cina:queue:ingestion:dlq`) |
| Production | `SQSQueue` | AWS SQS via aioboto3 | SQS DLQ with redrive policy |

**Selection:** `cina.yaml` → `ingestion.queue.backend: redis|sqs`

The factory function `build_queue_backend()` in `cina/ingestion/queue/__init__.py` reads the config and returns the appropriate implementation.

## Consequences

### Benefits

- **Backend-agnostic pipelines:** Ingestion pipeline and embedding workers depend only on `QueueProtocol`, not Redis or SQS APIs
- **Zero-code cloud migration:** Switching from local to AWS requires changing one config value (`backend: sqs`) and setting environment variables for SQS URLs
- **Consistent DLQ semantics:** Both implementations capture failed messages with reason strings, enabling the `cina dlq list/retry/purge` CLI commands to work uniformly
- **Fail-fast validation:** Missing SQS URLs or invalid Redis connections are caught at startup, not at first message

### Costs

- **Two implementations to maintain:** Adding a new queue operation requires updating both `RedisStreamQueue` and `SQSQueue`
- **Abstraction gap:** SQS has features (visibility timeout, redrive count) that don't map to Redis Streams natively; the protocol exposes the lowest common denominator
- **Testing surface:** Both backends need independent integration tests

## Rejected Alternatives

### 1. Direct SQS Calls in Pipeline Code

Embed SQS SDK calls directly in the ingestion pipeline.

**Rejected because:** Forces all local development to either use LocalStack (heavy, flaky) or mock SQS. Redis Streams provides a fast, reliable local queue with zero AWS dependency. The abstraction cost is minimal (one 4-method protocol).

### 2. Celery

Use Celery as the queue abstraction layer.

**Rejected because:** Celery brings a large dependency tree, its own worker process model, and significant configuration complexity. CINA's queue needs are simple (enqueue/dequeue/ack/dlq) — Celery's task routing, result backends, and middleware are unnecessary overhead. Also, Celery is used in FuelSense, and the portfolio should demonstrate different approaches.

### 3. In-Process Queue (asyncio.Queue)

Use Python's built-in async queue for local development.

**Rejected because:** No durability — if the process crashes during embedding, queued chunks are lost. Redis Streams provides persistence and consumer group semantics that closely match SQS behavior, making the local-to-production transition smoother.

### 4. Amazon MQ / RabbitMQ

Use a traditional message broker.

**Rejected because:** Adds infrastructure complexity for a use case that SQS handles natively. CINA doesn't need advanced routing, topic subscriptions, or message priority queues. SQS is simpler, cheaper, and fully managed.

## Decision Matrix

| Criteria (weight) | QueueProtocol | Direct SQS | Celery | asyncio.Queue | Amazon MQ |
|---|---|---|---|---|---|
| Local dev simplicity (25%) | 10 | 4 | 6 | 10 | 4 |
| Production readiness (25%) | 9 | 10 | 9 | 2 | 9 |
| DLQ consistency (20%) | 9 | 8 | 7 | 2 | 9 |
| Maintenance cost (15%) | 7 | 9 | 5 | 10 | 5 |
| Portfolio differentiation (15%) | 9 | 7 | 3 | 7 | 7 |
| **Weighted score** | **9.00** | **7.65** | **6.30** | **5.60** | **6.80** |

## Implementation Evidence

### Local Path (Redis Streams)

- Consumer group `cina-workers` with configurable concurrency (default: 8 workers)
- `XADD` for enqueue, `XREADGROUP` with block for dequeue, `XACK` for acknowledge
- DLQ implemented as a separate stream with `XADD` including reason metadata
- Bug fix during Phase 1: Changed consumer group start ID from `$` (new messages only) to `0` (read from beginning) to avoid missing pre-enqueued messages

### AWS Path (SQS)

- `send_message` for enqueue, `receive_message` with long poll (20s) for dequeue, `delete_message` for acknowledge
- Visibility timeout: 300s (allows time for batch embedding)
- DLQ forwarding via separate `send_message` to the DLQ URL with reason in message attributes
- Demo evidence: SQS enqueue verified — message consumed within 20s, queue depth returned to 0

### File References

- [`cina/ingestion/queue/protocol.py`](../../cina/ingestion/queue/protocol.py) — `QueueProtocol` definition
- [`cina/ingestion/queue/redis_stream.py`](../../cina/ingestion/queue/redis_stream.py) — Redis Streams implementation
- [`cina/ingestion/queue/sqs.py`](../../cina/ingestion/queue/sqs.py) — SQS implementation (aioboto3)
- [`cina/ingestion/queue/__init__.py`](../../cina/ingestion/queue/__init__.py) — `build_queue_backend()` factory
- [`cina/cli/dlq.py`](../../cina/cli/dlq.py) — DLQ management CLI commands
- [`docs/demo/ingestion-smoke.txt`](../demo/ingestion-smoke.txt) — SQS enqueue/consume evidence
