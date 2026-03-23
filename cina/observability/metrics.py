"""Prometheus metric definitions and exposition helper."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# Ingestion
cina_ingestion_documents_processed_total = Counter(
    "cina_ingestion_documents_processed_total",
    "Documents processed by source",
    ["source"],
)
cina_ingestion_chunks_created_total = Counter(
    "cina_ingestion_chunks_created_total",
    "Chunks created",
)
cina_ingestion_embedding_latency_seconds = Histogram(
    "cina_ingestion_embedding_latency_seconds",
    "Embedding latency seconds",
)
cina_ingestion_embedding_batch_size = Histogram(
    "cina_ingestion_embedding_batch_size",
    "Embedding batch size",
)
cina_ingestion_errors_total = Counter(
    "cina_ingestion_errors_total",
    "Ingestion errors",
    ["stage", "error_type"],
)
cina_ingestion_queue_depth = Gauge("cina_ingestion_queue_depth", "Ingestion queue depth")

# Query serving
cina_query_latency_seconds = Histogram(
    "cina_query_latency_seconds",
    "Query latency by stage",
    ["stage"],
)
cina_query_total = Counter("cina_query_total", "Total query count")
cina_rerank_latency_seconds = Histogram(
    "cina_rerank_latency_seconds",
    "Cross-encoder rerank latency",
)
cina_context_tokens_used = Histogram("cina_context_tokens_used", "Context tokens used")
cina_context_chunks_included = Histogram(
    "cina_context_chunks_included",
    "Context chunks included",
)

# Orchestration
cina_cache_requests_total = Counter(
    "cina_cache_requests_total",
    "Semantic cache hits/misses",
    ["result"],
)
cina_provider_request_total = Counter(
    "cina_provider_request_total",
    "Provider requests",
    ["provider", "status"],
)
cina_provider_latency_seconds = Histogram(
    "cina_provider_latency_seconds",
    "Provider latency",
    ["provider"],
)
cina_provider_fallback_total = Counter("cina_provider_fallback_total", "Fallback events")
cina_rate_limit_exceeded_total = Counter(
    "cina_rate_limit_exceeded_total",
    "Rate limit exceeded",
    ["tenant"],
)
cina_cost_usd_total = Counter(
    "cina_cost_usd_total",
    "Accumulated USD cost",
    ["provider", "tenant"],
)


METRIC_NAMES = [
    "cina_ingestion_documents_processed_total",
    "cina_ingestion_chunks_created_total",
    "cina_ingestion_embedding_latency_seconds",
    "cina_ingestion_embedding_batch_size",
    "cina_ingestion_errors_total",
    "cina_ingestion_queue_depth",
    "cina_query_latency_seconds",
    "cina_query_total",
    "cina_rerank_latency_seconds",
    "cina_context_tokens_used",
    "cina_context_chunks_included",
    "cina_cache_requests_total",
    "cina_provider_request_total",
    "cina_provider_latency_seconds",
    "cina_provider_fallback_total",
    "cina_rate_limit_exceeded_total",
    "cina_cost_usd_total",
]


def render_metrics() -> tuple[bytes, str]:
    """Render all registered Prometheus metrics with content-type."""
    return generate_latest(), CONTENT_TYPE_LATEST
