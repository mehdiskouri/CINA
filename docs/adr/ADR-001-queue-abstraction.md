# ADR-001: Queue Abstraction for Ingestion Workers

## Status
Accepted

## Context

CINA must support a local developer workflow and an AWS deployment workflow without rewriting
core ingestion logic.

- Local development uses Redis Streams.
- AWS deployment uses SQS with a DLQ redrive policy.
- Embedding workers should only depend on queue semantics, not queue-specific SDKs.

## Decision

Define and rely on a queue interface (`QueueProtocol`) with four operations:

- `enqueue(message, queue_name)`
- `dequeue(queue_name, wait_timeout_seconds)`
- `acknowledge(receipt)`
- `dead_letter(message, queue_name, reason)`

Implementations:

- `RedisStreamQueue` for local Docker Compose and CI.
- `SQSQueue` for AWS ECS/Fargate execution.

Selection is runtime-configurable via `cina.yaml` and env overrides:

- `ingestion.queue.backend=redis|sqs`

The ingestion pipeline builds the backend through `cina.ingestion.queue.build_queue_backend`.

## Consequences

Positive:

- Keeps ingestion and worker logic backend-agnostic.
- Enables cloud migration by changing configuration, not business logic.
- Supports reliability patterns (DLQ, retries) consistently.

Trade-offs:

- Small adapter maintenance overhead across two queue backends.
- Runtime configuration errors (missing SQS URLs) now fail fast at startup.

## Validation Evidence

- Local path remains unchanged with `RedisStreamQueue`.
- AWS path implemented with `SQSQueue` (`send_message`, `receive_message`, `delete_message`).
- Unit tests cover SQS enqueue/dequeue/ack/dead-letter behavior.
