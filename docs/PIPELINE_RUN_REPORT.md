# CINA Pipeline Run Report

**Date**: 2026-03-19  
**Environment**: Ubuntu 22.04, Python 3.13.12, PostgreSQL 14.22, pgvector 0.8.0, Redis 6.0.16  
**GPU**: CUDA-enabled (cross-encoder reranker)

---

## Phase 1 — Ingestion Pipeline

### Overview

The ingestion pipeline processes clinical literature from three sources, parsing documents into sections, chunking sections into token-bounded fragments, and embedding each chunk via the OpenAI API. A Redis stream queue decouples document parsing from embedding to handle rate limits gracefully.

### Pipeline Flow

```
Source Files (JSON/XML)
  → Connector (parse source-specific format → Document model)
    → Section extraction (title, abstract, body, tables, etc.)
      → Chunking engine (sentence-aligned, section-respecting, 512-token max)
        → Redis stream queue (enqueue chunk IDs + content)
          → Embedding worker (OpenAI text-embedding-3-large, 512 dims, batch=64)
            → PostgreSQL + pgvector (HNSW index, cosine similarity)
```

### Configuration

| Parameter | Value |
|---|---|
| Tokenizer | cl100k_base |
| Max chunk tokens | 512 |
| Overlap tokens | 64 |
| Sentence alignment | enabled |
| Section boundaries | respected |
| Embedding model | text-embedding-3-large |
| Embedding dimensions | 512 |
| Batch size | 64 |
| Concurrency | 8 |
| HNSW m | 16 |
| HNSW ef_construction | 200 |

### Data Sources

| Source | Files | Documents Ingested | Sections | Chunks Created | Chunks Embedded | Errors | Duration |
|---|---|---|---|---|---|---|---|
| clinicaltrials | 1,000 | 1,000 | — | 8,844 | 8,846 | 0 | 144s |
| pubmed | 2,000 | 2,000 | — | 71,316 | 73,598 | 0 | 1,262s (~21 min) |
| fda | 500 | 499 | — | 18,442 | ~20,158 | 1 | 358s (~6 min) |
| **Total** | **3,500** | **3,500** | **68,005** | **98,602** | **98,602** | **1** | **~29 min** |

> The single FDA error was a PostgreSQL deadlock during concurrent upsert — the affected document was still ingested on retry.
> Embedded counts slightly exceed chunk counts because some re-runs re-embedded previously queued chunks.

### Database State

| Table | Size |
|---|---|
| chunks | 903 MB |
| sections | 97 MB |
| documents | 2,448 kB |
| idx_chunks_embedding_hnsw | 263 MB |
| idx_chunks_fts (GIN) | 50 MB |

### Bug Fix During Run

The Redis stream consumer group was created with `id="$"` (only read new messages), which caused the embedding worker to miss all messages enqueued during the document-parsing phase. Fixed to `id="0"` (read from beginning) in `cina/ingestion/queue/redis_stream.py`.

---

## Phase 2 — Query Serving Pipeline

### Overview

The serving pipeline implements a RAG (Retrieval-Augmented Generation) architecture that takes a natural language query, retrieves relevant clinical literature via hybrid search, reranks results, assembles a token-budgeted context window, and streams an LLM-generated response with inline citations over Server-Sent Events (SSE).

### Pipeline Flow

```
User Query (POST /v1/query)
  → Query embedding (OpenAI text-embedding-3-large, 512d)
  → Parallel hybrid search:
      ├─ Vector search (pgvector HNSW, cosine similarity, top 50)
      └─ BM25 full-text search (PostgreSQL tsvector/GIN, top 50)
  → Reciprocal Rank Fusion (k=60)
  → Cross-encoder reranking (ms-marco-MiniLM-L-6-v2, top 20 → 10)
  → Context assembly (token budget, skip-and-try, max 15 chunks)
  → Prompt construction (system prompt + numbered sources)
  → Anthropic Claude Sonnet 4 streaming
  → SSE response (metadata → tokens → citations → metrics → done)
```

### Configuration

| Parameter | Value |
|---|---|
| Vector top_k | 50 |
| BM25 top_k | 50 |
| RRF k | 60 |
| HNSW ef_search | 100 |
| Rerank model | cross-encoder/ms-marco-MiniLM-L-6-v2 |
| Rerank candidates | 20 |
| Rerank top_n | 10 |
| Rerank device | CUDA (auto-detected) |
| Context max chunks | 15 |
| Generation buffer tokens | 2,048 |
| LLM provider | Anthropic |
| LLM model | claude-sonnet-4-20250514 |
| SSE keepalive interval | 15s |

### SSE Event Sequence

```
event: metadata
data: {"query_id": "...", "model": "claude-sonnet-4-20250514", "provider": "anthropic", "sources_used": 10, "cache_hit": false}

event: token          (× N, streaming)
data: {"text": "..."}

event: citations
data: {"citations": [{"index": 1, "document_title": "...", "source": "pubmed", ...}, ...]}

event: metrics
data: {"search_latency_ms": 7.5, "rerank_latency_ms": 74.4, "assembly_latency_ms": 4.9, "llm_ttft_ms": 1299.1, "llm_total_ms": 15017.7, "input_tokens": 4742, "output_tokens": 2794, "estimated_cost_usd": 0.056136}

event: done
data: {}
```

### Test Query Results

**Query**: "What are the latest treatments for metastatic breast cancer?"

| Stage | Latency |
|---|---|
| Hybrid search (vector + BM25 + RRF) | 7.5 ms |
| Cross-encoder rerank | 74.4 ms |
| Context assembly | 4.9 ms |
| LLM time-to-first-token | 1,299 ms |
| LLM total generation | 15,018 ms |

| Metric | Value |
|---|---|
| Sources cited | 10 |
| Input tokens | 4,742 |
| Output tokens | 2,794 |
| Estimated cost | $0.056 |
| SSE token events | 198 |

Sources retrieved spanned pubmed and fda, with citations referencing trastuzumab-based HER2+ therapy, CDK4/6 inhibitors, and breast cancer survivorship studies.

### Graceful Degradation

The pipeline handles failures at each stage:
- Vector search failure → falls back to BM25-only
- BM25 failure → falls back to vector-only
- Reranker failure → skips reranking, uses RRF order
- LLM failure → emits SSE error event

---

## Running the Pipeline

### Prerequisites

```bash
# PostgreSQL 14+ with pgvector extension
# Redis 6+
# Python 3.13+
# API keys in .env.keys.local (git-ignored):
#   OPENAI_API_KEY=sk-proj-...
#   ANTHROPIC_API_KEY=sk-ant-...
```

### Setup

```bash
# Install Python dependencies
pip install -e ".[dev]"

# Set environment
export DATABASE_URL="postgresql://cina:cina_dev@localhost:5432/cina"
export REDIS_URL="redis://localhost:6379/0"
source .env.keys.local

# Run migrations
python -m cina db migrate
```

### Phase 1 — Ingestion

```bash
# Ingest each source (runs parsing + chunking + embedding)
python -m cina ingest run --source clinicaltrials --path data/clinicaltrials
python -m cina ingest run --source pubmed --path data/pubmed
python -m cina ingest run --source fda --path data/fda
```

### Phase 2 — Serving

```bash
# Start the API server
python -m cina serve --port 8000

# Test a query
curl -N -X POST http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the latest treatments for metastatic breast cancer?"}'
```

---

## Database Backup

A full `pg_dump` (custom format, gzip-compressed) of the `cina` database is saved at the project root.

| File | Size | MD5 | Contents |
|---|---|---|---|
| `cina_db_dump.sql.gz` | 316 MB | `a14713ad5bb6c3611e43ce029182842d` | 3,500 documents, 68,005 sections, 98,602 chunks with 512-d embeddings, HNSW + GIN indexes |

> **Note**: This file is gitignored due to size. It lives on the local machine at the project root.

### Creating the dump

```bash
PGPASSWORD=cina_dev pg_dump -U cina -d cina -h localhost --format=custom \
  | gzip > cina_db_dump.sql.gz
```

### Restoring from the dump

```bash
# Ensure the database and pgvector extension exist
sudo -u postgres psql -c "CREATE DATABASE cina OWNER cina;"
sudo -u postgres psql -d cina -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d cina -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# Restore
gunzip -c cina_db_dump.sql.gz | pg_restore -U cina -d cina -h localhost --no-owner --clean 2>/dev/null
```
