"""Queue backend selection and exports for ingestion workers."""

from __future__ import annotations

import os

from cina.config import load_config
from cina.ingestion.queue.protocol import QueueProtocol
from cina.ingestion.queue.redis_stream import RedisStreamQueue
from cina.ingestion.queue.sqs import SQSQueue


def build_queue_backend() -> QueueProtocol:
    """Build the configured ingestion queue backend implementation."""
    cfg = load_config()
    backend = cfg.ingestion.queue.backend.strip().lower()

    if backend == "redis":
        redis_url = os.getenv(cfg.database.redis.url_env, "redis://localhost:6379/0")
        return RedisStreamQueue(redis_url=redis_url)

    if backend == "sqs":
        return SQSQueue(
            queue_url_env=cfg.ingestion.queue.sqs_url_env,
            dlq_url_env=cfg.ingestion.queue.sqs_dlq_url_env,
            region_env=cfg.ingestion.queue.sqs_region_env,
            endpoint_url_env=cfg.ingestion.queue.sqs_endpoint_url_env,
        )

    message = f"Unsupported ingestion queue backend: {cfg.ingestion.queue.backend}"
    raise ValueError(message)


__all__ = ["QueueProtocol", "RedisStreamQueue", "SQSQueue", "build_queue_backend"]
