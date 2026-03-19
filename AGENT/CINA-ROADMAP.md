# CINA — Implementation Roadmap

## Clinical Index & Narrative Assembly

**Version:** 1.0
**Author:** Mehdi
**Date:** March 2026
**Status:** Pre-Implementation
**Companion Documents:** [CINA-PRD.md](./CINA-PRD.md) · [CINA-ARCHITECTURE.md](./CINA-ARCHITECTURE.md)

---

## Table of Contents

1. [Roadmap Overview](#1-roadmap-overview)
2. [Machine Specifications](#2-machine-specifications)
3. [Pre-Implementation Setup](#3-pre-implementation-setup)
4. [Phase 1: Ingestion Pipeline](#4-phase-1-ingestion-pipeline)
5. [Phase 2: Query Serving Layer](#5-phase-2-query-serving-layer)
6. [Phase 3: Orchestration Hardening](#6-phase-3-orchestration-hardening)
7. [Phase 4: AWS Deployment Proof](#7-phase-4-aws-deployment-proof)
8. [Phase 5: Documentation & Portfolio Polish](#8-phase-5-documentation--portfolio-polish)
9. [Risk Register](#9-risk-register)
10. [API Cost Projections](#10-api-cost-projections)
11. [Milestone Checklist](#11-milestone-checklist)

---

## 1. Roadmap Overview

### 1.1 Phase Map

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          CINA IMPLEMENTATION TIMELINE                    │
├──────────┬──────────┬──────────┬──────────┬──────────┬──────────────────┤
│  Pre-    │ Phase 1  │ Phase 2  │ Phase 3  │ Phase 4  │    Phase 5      │
│  Setup   │ Ingest   │ Serving  │ Orchestr │ AWS Demo │    Polish       │
│          │          │          │          │          │                  │
│  ~2 days │ ~8 days  │ ~8 days  │ ~6 days  │ ~3 days  │    ~3 days     │
├──────────┼──────────┼──────────┼──────────┼──────────┼──────────────────┤
│  LIGHT   │  LIGHT   │  HEAVY   │  LIGHT   │  LIGHT   │    LIGHT        │
│  CPU     │  CPU     │  GPU     │  CPU     │  CPU     │    CPU          │
└──────────┴──────────┴──────────┴──────────┴──────────┴──────────────────┘
                                     ▲
                                     │
                           Only phase requiring GPU
                           (cross-encoder benchmarking)
```

**Total estimated duration:** ~30 working days (6 weeks at ~5 days/week).

This is a working estimate, not a commitment. Each phase has explicit exit criteria. Move to the next phase only when all exit criteria are met — don't rush through a phase to stay on schedule. A phase that's solid and documented is worth more than two phases that are fragile.

### 1.2 Machine Strategy

CINA is designed so that only one phase (Phase 2) requires a GPU machine. Every other phase runs comfortably on a modest CPU instance. The roadmap is structured to minimize time on expensive hardware:

- **Light phases** (Pre-Setup, Phase 1, Phase 3, Phase 4, Phase 5): CPU-only Vast.ai instance. All development, testing, Docker Compose infrastructure, Terraform work, and documentation.
- **Heavy phase** (Phase 2): GPU Vast.ai instance. Cross-encoder loading, inference benchmarking, latency profiling, and the re-ranking quality benchmark for ADR-3.

The transition between machines is a single `git push` / `git pull`. All state lives in the repository and in the Docker Compose volumes (PostgreSQL, Redis). The populated pgvector index from Phase 1 is either rebuilt on the GPU machine (takes ~10–20 minutes with pre-downloaded data and Bedrock's higher TPM limits) or transferred as a PostgreSQL dump.

---

## 2. Machine Specifications

### 2.1 Light Machine — CPU Development

**Used during:** Pre-Setup, Phase 1, Phase 3, Phase 4, Phase 5 (~22 of 30 days)

**Vast.ai search filters:**
- CPU: 4+ cores (8 preferred)
- RAM: 16 GB minimum (32 GB preferred)
- Disk: 50 GB SSD minimum (100 GB preferred for PubMed data)
- GPU: none required
- Network: unmetered (PubMed bulk download is ~1 GB, Docker images are ~2 GB)

**Recommended Vast.ai instance type:**
- **Budget option:** 4-core CPU, 16 GB RAM, 50 GB SSD — typically $0.03–0.06/hr (~$1–1.50/day)
- **Comfort option:** 8-core CPU, 32 GB RAM, 100 GB SSD — typically $0.06–0.12/hr (~$2–3/day)

**Why this is enough:**
- Docker Compose (PostgreSQL + Redis + Prometheus + Grafana) runs in ~1.5 GB RAM
- Document parsing (lxml on XML) is single-threaded per document, parallelized across cores
- Embedding generation is API-bound (network I/O), not CPU-bound
- Cross-encoder runs on CPU in Phase 1 unit tests (slow but functional — you're not benchmarking latency here)
- Terraform plan/apply are lightweight CLI operations
- All async Python (FastAPI, asyncpg, httpx) is I/O-bound, not CPU-bound

**Software stack to install:**
```bash
# System
sudo apt update && sudo apt install -y docker.io docker-compose-v2 postgresql-client-16

# Python (via pyenv, already in your workflow)
pyenv install 3.12.8
pyenv local 3.12.8

# Project tooling
pip install uv  # fast package manager
uv sync         # install from pyproject.toml lockfile

# Node (only if needed for any tooling)
# Not required for CINA — pure Python project

# Terraform
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install terraform

# AWS CLI (required from Phase 1 — Bedrock embeddings need AWS credentials)
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip && sudo ./aws/install

# AWS credentials — configure for Bedrock access
aws configure
# Set: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, region (us-east-1)
# Verify Bedrock access:
aws bedrock-runtime invoke-model \
  --model-id amazon.titan-embed-text-v2:0 \
  --body '{"inputText": "test", "dimensions": 512}' \
  --region us-east-1 /dev/stdout | head -c 100
```

**Note on AWS credentials:** Unlike the original design where AWS was only needed for production deployment, the switch to Bedrock embeddings means AWS credentials are required from Phase 1 onward in all environments. Use an IAM user with a policy scoped to `bedrock:InvokeModel` on Titan Embed V2. The same credentials can be extended for SQS and S3 access in Phase 4.

### 2.2 Heavy Machine — GPU Development & Benchmarking

**Used during:** Phase 2 only (~8 of 30 days)

**Vast.ai search filters:**
- GPU: 1x RTX 4090 or RTX 5090 (24 GB VRAM — more than enough for MiniLM cross-encoder at ~250 MB)
- CPU: 8+ cores
- RAM: 32 GB minimum
- Disk: 100 GB SSD (need room for pgvector data + model weights + Docker volumes)
- CUDA: 12.x
- Docker pre-installed: strongly preferred

**Recommended Vast.ai instance type:**
- **RTX 4090:** Typically $0.30–0.50/hr (~$4–6/day). Ample for MiniLM-L-6 and even larger cross-encoders.
- **RTX 5090:** Typically $0.50–0.80/hr (~$6–10/day). Overkill for MiniLM-L-6, but useful if benchmarking larger cross-encoders (L-12, or full-size models) for ADR-3 comparison data.
- **RTX 3090:** Budget fallback at $0.15–0.25/hr. 24 GB VRAM, slightly older CUDA cores. Perfectly adequate.

**Why GPU matters here:**
- Cross-encoder re-ranking: 20 candidates × MiniLM-L-6 takes ~50ms on GPU vs. ~500ms on CPU. You need GPU to hit the p95 < 100ms performance budget.
- Benchmarking ADR-3 requires accurate latency numbers under realistic conditions. CPU numbers are not representative of production behavior.
- Benchmarking larger cross-encoders (L-12, full-size) for the ADR comparison table requires GPU to be practical.

**Why only Phase 2:**
- Phase 1 (ingestion) has no model inference. Embedding is done via API calls.
- Phase 3 (orchestration) tests cache, rate limiting, circuit breakers — all CPU-bound or I/O-bound. The cross-encoder is already integrated in Phase 2; Phase 3 just wraps it in middleware.
- Phase 4 (AWS) runs on Fargate, not your local machine.
- Phase 5 (documentation) is text.

**Additional setup on GPU machine:**
```bash
# Verify CUDA
nvidia-smi  # confirm GPU is visible
python -c "import torch; print(torch.cuda.is_available())"  # confirm PyTorch sees it

# Install sentence-transformers with CUDA support
pip install sentence-transformers torch --extra-index-url https://download.pytorch.org/whl/cu121

# Pull cross-encoder weights (do this early — ~250 MB download)
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-12-v2')"
```

### 2.3 Machine Transition Protocol

Moving from Light → Heavy (start of Phase 2):
```bash
# On Light machine: commit and push everything
git add -A && git commit -m "Phase 1 complete" && git push

# Export PostgreSQL data (populated index from Phase 1)
docker compose exec postgres pg_dump -U cina cina > cina_phase1.sql
# Upload dump to persistent storage (or keep in repo as gzipped artifact)
gzip cina_phase1.sql

# On Heavy machine: clone and restore
git clone <repo> && cd cina
docker compose up -d postgres redis
docker compose exec -T postgres psql -U cina cina < cina_phase1.sql.gz
# Or: re-run ingestion (~10-20 min with pre-downloaded data and Bedrock, regenerates index fresh)
```

Moving from Heavy → Light (end of Phase 2):
```bash
# On Heavy machine: commit benchmarks, results, and code
git add -A && git commit -m "Phase 2 complete — benchmarks in docs/" && git push

# No data transfer needed — Phase 3 onward doesn't need the GPU
# Docker Compose on Light machine rebuilds the index if needed for integration tests
```

---

## 3. Pre-Implementation Setup

**Machine:** Light (CPU)
**Duration:** ~2 days
**Goal:** Repository skeleton, tooling, Docker Compose infrastructure, and database schema deployed and verified. Zero application code — pure scaffolding.

### 3.1 Task Breakdown

#### Day 1: Repository & Tooling

**Task 3.1.1 — Initialize repository**
- Create repo with `.gitignore` (Python, Docker, Terraform, IDE)
- Initialize `pyproject.toml` with `uv` as package manager
- Pin Python 3.12, set project metadata
- Add all core dependencies from Architecture Document § 18.1
- Run `uv sync` to generate lockfile
- Verify: `uv run python -c "import fastapi, asyncpg, redis, lxml, tiktoken; print('ok')"`

**Task 3.1.2 — Create package skeleton**
- Build the full module structure from Architecture Document § 14
- Every `__init__.py` in place, every directory created
- Add empty `Protocol` classes in protocol files (stubs, not implementations)
- Verify: `uv run python -c "from cina.ingestion.queue.protocol import QueueProtocol; print('ok')"`

**Task 3.1.3 — Set up development tooling**
- `ruff` for linting and formatting (replaces black + isort + flake8)
- `mypy` strict mode configuration in `pyproject.toml`
- `pytest` configuration with `pytest-asyncio`
- `pre-commit` hooks: ruff, mypy, pytest (fast unit tests only)
- `Makefile` with targets: `lint`, `typecheck`, `test`, `test-integration`, `serve`, `ingest`
- Verify: `make lint && make typecheck` passes on the empty skeleton

**Task 3.1.4 — Configuration system**
- Implement `cina/config/loader.py`: YAML file loading + environment variable overlay
- Implement `cina/config/schema.py`: Pydantic settings models matching Architecture Document § 9.2
- Create `cina.yaml` with development defaults
- Create `.env.example` with all required environment variables documented (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, `DATABASE_URL`, `REDIS_URL`)
- Verify: `uv run python -c "from cina.config import load_config; c = load_config(); print(c.serving.search.rrf_k)"`

#### Day 2: Infrastructure & Schema

**Task 3.2.1 — Docker Compose**
- Create `docker-compose.yml` matching Architecture Document § 12.1
- PostgreSQL 16 with pgvector, Redis 7, Prometheus, Grafana
- Volume mounts for Prometheus config and Grafana dashboards/provisioning
- Create `infra/prometheus.yml` with scrape targets (placeholder ports)
- Create `infra/grafana/provisioning/` with datasource and dashboard provisioning
- Verify: `docker compose up -d && docker compose ps` shows all services healthy

**Task 3.2.2 — Database migrations**
- Create `cina/db/migrations/001_initial_schema.sql` containing the full schema from Architecture Document § 4.2
- Implement simple migration runner in `cina/cli/db.py` (reads `.sql` files in order, tracks applied migrations in a `schema_migrations` table)
- Run migrations: `uv run python -m cina db migrate`
- Verify: connect to PostgreSQL, confirm all tables, indexes, and pgvector extension exist

**Task 3.2.3 — Database connection pool**
- Implement `cina/db/connection.py`: asyncpg pool creation/shutdown lifecycle
- Tie pool to FastAPI lifespan events (startup/shutdown)
- Implement basic health check query (`SELECT 1`)
- Verify: `uv run python -c "import asyncio; from cina.db import get_pool; asyncio.run(get_pool())"` connects

**Task 3.2.4 — Observability skeleton**
- Implement `cina/observability/metrics.py`: define all Prometheus counters/histograms from PRD § 4d (empty, no instrumentation yet — just the metric objects)
- Implement `cina/observability/logging.py`: structlog configuration with JSON output and correlation ID context variable
- Implement `cina/api/middleware/correlation.py`: correlation ID middleware
- Verify: Prometheus `/metrics` endpoint returns metric names (all zero), structured logs emit valid JSON

**Task 3.2.5 — CLI skeleton**
- Implement `cina/cli/main.py` using Typer
- Subcommands: `cina ingest`, `cina serve`, `cina db`, `cina apikey`, `cina dlq`
- All subcommands are stubs that print "not implemented" — implementation comes in later phases
- Verify: `uv run python -m cina --help` shows all commands

### 3.2 Exit Criteria

- [ ] `make lint && make typecheck` passes
- [ ] `docker compose up -d` starts PostgreSQL (pgvector), Redis, Prometheus, Grafana
- [ ] `cina db migrate` creates all tables and indexes
- [ ] Configuration loads from YAML with env var overrides
- [ ] Prometheus endpoint returns defined metrics
- [ ] Structured JSON logs emit with correlation IDs
- [ ] CLI shows all subcommand groups
- [ ] All protocol stubs are importable with no circular dependencies

---

## 4. Phase 1: Ingestion Pipeline

**Machine:** Light (CPU)
**Duration:** ~8 days
**Goal:** End-to-end document ingestion from raw source files to searchable pgvector index with full metadata lineage. Chunking benchmark completed for ADR-6.

### 4.1 Task Breakdown

#### Days 3–4: Data Acquisition & Document Parsing

**Task 4.1.1 — Acquire sample data**
- Download PubMed Central OA subset: select 2,000–3,000 articles across cardiology, oncology, infectious disease, endocrinology via PMC FTP. Use `rsync` or `wget` with file list filtering.
- Download 500 FDA drug labels from DailyMed bulk download (choose common therapeutic classes).
- Download 1,000 ClinicalTrials.gov records via API v2 (completed studies with results).
- Store raw data in `./data/{pubmed,fda,clinicaltrials}/` (gitignored).
- Verify: file counts match targets, sample files open and contain expected XML/JSON structure.

**Task 4.1.2 — Implement data models**
- Implement `cina/models/document.py`: `Document`, `Section`, `Chunk`, `RawDocument` as frozen dataclasses
- Implement `cina/models/search.py`: `SearchResult`, `RankedResult`
- All fields typed, all with `__eq__` and `__hash__` for deduplication
- Verify: `make typecheck` passes

**Task 4.1.3 — PubMed Central connector**
- Implement `cina/ingestion/connectors/pubmed.py`
- JATS XML parsing with `lxml`: extract title, authors, publication date, abstract, all `<sec>` elements
- Handle nested sections (flatten with composite headings)
- Strip figure/table captions into separate sections
- Handle edge cases: missing sections, empty content, malformed XML (log warning, skip document)
- Unit tests: parse 5 known articles, verify section count, types, and content extraction

**Task 4.1.4 — FDA DailyMed connector**
- Implement `cina/ingestion/connectors/fda.py`
- SPL XML parsing: extract drug name, sections (Indications, Dosage, Contraindications, Warnings, Adverse Reactions, Drug Interactions)
- Preserve structured dosage data in `raw_metadata`
- Unit tests: parse 5 known drug labels, verify section mapping

**Task 4.1.5 — ClinicalTrials.gov connector**
- Implement `cina/ingestion/connectors/clinicaltrials.py`
- JSON field mapping to sections (Brief Summary → abstract, Eligibility → eligibility, etc.)
- Handle missing fields gracefully (many trials have sparse data)
- Unit tests: parse 5 known trial records, verify field extraction

#### Days 5–6: Chunking Engine & Embedding

**Task 4.2.1 — Sentence splitter**
- Implement `cina/ingestion/chunking/sentences.py`
- Rule-based splitter with medical abbreviation awareness (i.v., p.o., b.i.d., Dr., Fig., et al., etc.)
- Handles period-heavy abbreviations without false splits
- Unit tests: 20+ test cases covering abbreviations, multi-sentence paragraphs, edge cases (empty strings, single sentences, sentences ending with abbreviations)

**Task 4.2.2 — Two-pass chunking engine**
- Implement `cina/ingestion/chunking/engine.py`
- Pass 1: section-aware splitting (sections under budget → single chunk)
- Pass 2: sliding window with overlap for oversized sections, sentence-aligned
- Token counting via `tiktoken` (`cl100k_base` encoding)
- Content hash generation (`sha256(content + embedding_model)`)
- Metadata propagation: each chunk carries section_type, heading, document title, authors, source
- Unit tests:
  - Short section → single chunk, no overlap
  - Long section → multiple chunks with correct overlap
  - Section exactly at budget → single chunk (boundary case)
  - Document with mixed short/long sections → correct chunk count
  - Determinism: same input → same chunks (test with multiple runs)
  - Token count accuracy: verify against manual count on known strings

**Task 4.2.3 — Embedding provider (Bedrock)**
- Implement `cina/ingestion/embedding/protocol.py`: `EmbeddingProviderProtocol` with `embed` and `health_check` methods
- Implement `cina/ingestion/embedding/bedrock.py`: Bedrock Titan V2 implementation
- Async `aioboto3` client calling Bedrock `invoke_model` endpoint
- Batched requests (configurable batch size, default 64)
- Token bucket rate limiter matching Bedrock service quota
- Exponential backoff with jitter on ThrottlingException/5xx errors
- Configurable output dimensions (256, 512, 1024) — default 512
- Returns list of embedding vectors (list[list[float]])
- Integration test: embed 10 known strings via Bedrock, verify dimension = 512, vectors are non-zero

**Task 4.2.4 — Embedding worker**
- Implement `cina/ingestion/embedding/worker.py`
- Consumes chunks from queue, batches, embeds, writes to database
- Idempotency check: skip chunks with existing `content_hash + embedding_model` entries
- Dead-letter routing after max retries
- Metrics: `cina_ingestion_embedding_latency_seconds`, `cina_ingestion_embedding_batch_size`

#### Days 7–8: Queue, Repository, Pipeline Assembly & Benchmark

**Task 4.3.1 — Redis Streams queue implementation**
- Implement `cina/ingestion/queue/redis_stream.py`
- `enqueue`: `XADD` to stream
- `dequeue`: `XREADGROUP` with consumer group
- `acknowledge`: `XACK`
- `dead_letter`: `XADD` to DLQ stream with failure metadata
- Consumer group creation on first use
- Integration test: enqueue 100 messages, consume all, acknowledge, verify empty stream

**Task 4.3.2 — Document repository**
- Implement `cina/db/repositories/document.py`
- `insert_document`: upsert on `(source, source_id)` unique constraint
- `insert_sections`: bulk insert with document_id FK
- `get_document_by_source_id`: lookup for idempotency check

**Task 4.3.3 — Chunk repository**
- Implement `cina/db/repositories/chunk.py`
- `bulk_upsert`: insert chunks with embeddings, skip on `(content_hash, embedding_model)` conflict
- `vector_search`: pgvector cosine similarity query (Architecture Document § 6.3)
- `bm25_search`: PostgreSQL `ts_rank_cd` query (Architecture Document § 6.3)
- `get_by_ids`: batch fetch chunks by ID (for re-ranking stage)
- Integration test: insert 100 chunks with embeddings, verify vector search returns sorted results

**Task 4.3.4 — Ingestion pipeline orchestrator**
- Implement `cina/ingestion/pipeline.py`
- Wires together: connector → parser → chunker → queue → embedding worker → repository
- Creates `IngestionJob` record, updates progress counters
- Configurable concurrency (default: 8 concurrent documents)
- Error handling: individual document failures don't kill the pipeline — log, increment error counter, continue

**Task 4.3.5 — CLI ingestion command**
- Implement `cina/cli/ingest.py`
- `cina ingest --source pubmed --path ./data/pubmed/ --batch-size 64 --concurrency 8`
- `cina ingest --source fda --path ./data/fda/`
- `cina ingest --source clinicaltrials --path ./data/clinicaltrials/`
- Progress bar via `rich` (documents processed, chunks created, errors)
- Verify: run full ingestion of PubMed sample data, watch progress, confirm database population

**Task 4.3.6 — Ingestion integration test**
- End-to-end test: ingest 50 known PubMed articles
- Verify: correct document count in DB, correct section count, correct chunk count
- Verify: every chunk has a non-null embedding of dimension 512
- Verify: metadata lineage — pick a random chunk, trace back to section and document
- Verify: re-ingestion of same 50 articles creates no new chunks (idempotency)

**Task 4.3.7 — Chunking benchmark (ADR-6)**
- Ingest same 200-article subset with two configurations:
  - Config A: structure-aware chunking (respect_sections=true)
  - Config B: naive fixed-window chunking (respect_sections=false, same token budget)
- Prepare 30 curated clinical queries with manually judged relevant documents
- Run vector search (top-10) against both indexes
- Measure precision@10, recall@10 for both configurations
- Document results in `docs/adr/ADR-006-chunking-strategy.md`
- This benchmark runs on CPU. Vector search quality is independent of search speed — you're measuring retrieval relevance, not latency.

### 4.2 Exit Criteria

- [ ] Three source connectors parse their respective formats correctly (unit tests passing)
- [ ] Chunking engine produces structure-aware chunks with correct token counts (unit tests passing)
- [ ] Full ingestion pipeline processes 1,000+ PubMed articles end-to-end
- [ ] Every chunk in pgvector has: valid embedding, correct metadata, traceable lineage
- [ ] Re-ingestion is idempotent (no duplicate chunks on repeated runs)
- [ ] Redis Streams queue works with consumer group acknowledgment
- [ ] Chunking benchmark completed and ADR-6 written with results
- [ ] Ingestion throughput: ≥ 100 documents/minute (Bedrock embedding API is the bottleneck)
- [ ] All unit and integration tests passing: `make test && make test-integration`

---

## 5. Phase 2: Query Serving Layer

**Machine:** Heavy (GPU)
**Duration:** ~8 days
**Goal:** Real-time query endpoint with hybrid search, cross-encoder re-ranking, context assembly, and streaming SSE. Re-ranking benchmark completed for ADR-3.

### 5.1 Machine Setup (Day 1 of Phase 2)

Before writing any Phase 2 code, set up the GPU machine:

```bash
# Clone repo, install dependencies
git clone <repo> && cd cina
uv sync
# Also install GPU-specific deps
pip install sentence-transformers torch --extra-index-url https://download.pytorch.org/whl/cu121

# Start Docker Compose
docker compose up -d

# Restore Phase 1 database (or re-ingest)
# Option A: restore dump
gunzip -c cina_phase1.sql.gz | docker compose exec -T postgres psql -U cina cina
# Option B: re-ingest (slower but guaranteed clean)
cina ingest --source pubmed --path ./data/pubmed/ --batch-size 64

# Verify GPU
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, Device: {torch.cuda.get_device_name(0)}')"

# Pre-download cross-encoder models
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', device='cuda')"
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-12-v2', device='cuda')"
```

### 5.2 Task Breakdown

#### Days 9–10: Hybrid Search

**Task 5.1.1 — Vector search implementation**
- Implement `cina/serving/search/vector.py`
- Async pgvector cosine similarity query via asyncpg
- Configurable `ef_search` parameter set at session level
- Returns `list[SearchResult]` with chunk content, metadata, and similarity score
- Integration test: query known medical terms, verify relevant chunks are returned

**Task 5.1.2 — BM25 search implementation**
- Implement `cina/serving/search/bm25.py`
- PostgreSQL `ts_rank_cd` with `plainto_tsquery`
- Medical-domain stop word configuration (optional: custom text search configuration)
- Returns `list[SearchResult]` with chunk content, metadata, and BM25 score
- Integration test: query exact drug names (e.g., "metformin"), verify exact-match results appear that vector search might miss

**Task 5.1.3 — Reciprocal Rank Fusion**
- Implement `cina/serving/search/fusion.py`
- RRF algorithm from Architecture Document § 6.4
- Generic over any number of ranked lists (not hardcoded to 2)
- Configurable `k` parameter
- Unit tests:
  - Two identical lists → top result has highest score
  - Disjoint lists → interleaved correctly
  - Single list → returned unchanged
  - Empty list handling

**Task 5.1.4 — Hybrid search orchestrator**
- Implement concurrent execution in `cina/serving/pipeline.py` (partial — search stage only)
- `asyncio.gather` for vector + BM25 concurrent queries
- RRF fusion of results
- Truncation to rerank candidate count
- Metrics: `cina_query_latency_seconds{stage="search"}`, `cina_search_results_count`
- Integration test: end-to-end hybrid search returns results from both retrieval paths

#### Days 11–12: Cross-Encoder & Context Assembly

**Task 5.2.1 — Cross-encoder re-ranker**
- Implement `cina/serving/rerank/cross_encoder.py`
- Model loaded at initialization, held in memory
- Auto-detect device (CUDA if available, CPU fallback)
- `run_in_executor` wrapping for async compatibility
- Configurable candidate count and top_n output
- Metrics: `cina_rerank_latency_seconds`
- Integration test: verify re-ranking changes result order (top result after re-ranking differs from top RRF result on at least some queries)

**Task 5.2.2 — Cross-encoder latency profiling**
- Profile MiniLM-L-6 on GPU: 10, 20, 30, 50 candidates
- Profile MiniLM-L-12 on GPU: same candidate counts
- Profile MiniLM-L-6 on CPU: 10, 20 candidates (for comparison)
- Record p50, p95, p99 latency for each configuration
- Results go into `docs/benchmarks/rerank_latency.md`

**Task 5.2.3 — Token budget context assembly**
- Implement `cina/serving/context/assembler.py`
- Greedy packing with skip-and-try (Architecture Document § 6.6)
- Token counting via `tiktoken`
- Max chunks hard cap
- Returns `list[NumberedSource]` with citation metadata
- Unit tests:
  - Exact budget fit (all chunks fit)
  - Over budget (some chunks skipped)
  - Skip-and-try: large chunk skipped, smaller one included
  - Max chunks cap: more eligible chunks than the cap → capped
  - Zero budget (system prompt fills entire context) → empty sources
  - Single chunk larger than budget → empty sources

**Task 5.2.4 — Prompt construction**
- Implement `cina/serving/context/prompt.py`
- Builds messages array with system prompt, numbered sources, and query
- Template matches Architecture Document § 6.7
- Unit test: verify message structure, source numbering, token count is within budget

#### Days 13–14: Streaming SSE & API Endpoint

**Task 5.3.1 — SSE event formatting**
- Implement `cina/serving/stream/sse.py`
- SSE event builder: formats `event: {type}\ndata: {json}\n\n`
- Event types: `metadata`, `token`, `citations`, `metrics`, `done`
- Keepalive events (`:keepalive\n\n` every 15 seconds) to prevent connection drops
- Unit test: verify event formatting matches SSE spec

**Task 5.3.2 — LLM provider stubs (temporary)**
- Implement minimal `AnthropicProvider` in `cina/orchestration/providers/anthropic.py`
- Streaming completion via `httpx.AsyncClient`
- No fallback, no circuit breaker (Phase 3)
- Just enough to get tokens streaming through the pipeline
- Integration test: send a simple prompt, verify streaming tokens are received

**Task 5.3.3 — Query pipeline orchestrator**
- Implement full `cina/serving/pipeline.py`
- Wires together: embed query → hybrid search → RRF → rerank → context assembly → prompt build → LLM stream → SSE formatting
- Correlation ID propagation through all stages
- Per-stage latency timing captured in metrics
- Error handling: graceful degradation per Architecture Document § 10.4

**Task 5.3.4 — FastAPI query endpoint**
- Implement `cina/api/routes/query.py`
- `POST /v1/query` with request validation (Pydantic model)
- `StreamingResponse` with `text/event-stream` content type
- `Cache-Control: no-cache` and `X-Accel-Buffering: no` headers
- Implement `cina/api/routes/health.py`: `/health` and `/ready` endpoints
- Implement `cina/api/app.py`: FastAPI application factory with lifespan (pool creation, model loading)

**Task 5.3.5 — CLI serve command**
- Implement `cina/cli/serve.py`
- `cina serve --port 8000 --reload`
- Wraps `uvicorn` with CINA configuration
- Verify: `cina serve` starts, `/health` returns 200, `/ready` returns 200

**Task 5.3.6 — End-to-end query integration test**
- Start full stack: Docker Compose + `cina serve`
- Send clinical query via `curl -N`
- Verify SSE stream: `metadata` event fires with correct fields, `token` events stream incrementally, `citations` event contains valid source references, `metrics` event contains per-stage latencies, `done` event terminates stream
- Verify: curl latency (excluding LLM) is under 500ms

#### Days 15–16: Benchmarks & ADRs

**Task 5.4.1 — Re-ranking quality benchmark (ADR-3)**
- Use same 30 curated clinical queries from Phase 1 chunking benchmark
- Run three configurations against the populated index:
  - Config A: RRF only (no cross-encoder)
  - Config B: RRF + MiniLM-L-6 re-ranking
  - Config C: RRF + MiniLM-L-12 re-ranking
- Measure nDCG@10, precision@10 for each
- Combine with latency data from Task 5.2.2
- Document results in `docs/adr/ADR-003-reranking-approach.md`
- Include the quality vs. latency tradeoff table and the decision justification

**Task 5.4.2 — Query latency profiling**
- Run 100 diverse clinical queries against the full pipeline
- Capture per-stage latency breakdown (search, rerank, assembly, LLM TTFT, LLM total)
- Generate p50, p95, p99 tables
- Document in `docs/benchmarks/query_latency.md`
- Verify p95 pre-LLM latency is under 500ms target

**Task 5.4.3 — Hybrid search effectiveness benchmark (ADR-2)**
- Run 30 queries that include exact medical terms (drug names, dosages, gene IDs)
- Compare: vector-only, BM25-only, hybrid (RRF fusion)
- Measure which queries are only answered correctly by the hybrid approach
- Document in `docs/adr/ADR-002-hybrid-search.md`

### 5.3 Exit Criteria

- [ ] `POST /v1/query` accepts clinical question and streams cited SSE response
- [ ] Hybrid search returns results from both vector and BM25 paths
- [ ] Cross-encoder re-ranking runs on GPU with p95 < 100ms for 20 candidates
- [ ] Context assembly respects token budget with skip-and-try strategy
- [ ] SSE stream follows correct event protocol (metadata → tokens → citations → metrics → done)
- [ ] Citations map to actual source documents with correct metadata
- [ ] Pre-LLM query latency p95 < 500ms
- [ ] End-to-end query latency p95 < 3 seconds (including LLM)
- [ ] ADR-2 (hybrid search), ADR-3 (re-ranking) written with benchmark evidence
- [ ] Query latency benchmark documented
- [ ] All unit and integration tests passing

---

## 6. Phase 3: Orchestration Hardening

**Machine:** Light (CPU)
**Duration:** ~6 days
**Goal:** Production-grade middleware — provider abstraction, fallback, caching, rate limiting, cost tracking, full observability. The cross-encoder runs on CPU here (slower, but Phase 3 is testing orchestration logic, not inference speed).

### 6.1 Task Breakdown

#### Days 17–18: Provider Abstraction & Fallback

**Task 6.1.1 — Provider protocol and implementations**
- Finalize `cina/orchestration/providers/protocol.py`: `LLMProviderProtocol`
- Implement `cina/orchestration/providers/openai.py`: full OpenAI streaming provider
- Refine `cina/orchestration/providers/anthropic.py`: full Anthropic streaming provider (upgrade from Phase 2 stub)
- Both implement `complete`, `health_check`, `estimate_cost`
- Unified error types: `ProviderTimeoutError`, `ProviderRateLimitError`, `ProviderServerError`
- Integration test: each provider streams a response to a simple prompt

**Task 6.1.2 — Circuit breaker**
- Implement `cina/orchestration/routing/circuit_breaker.py`
- Redis-backed state machine (closed → open → half-open → closed)
- Configurable failure threshold and cooldown period
- Unit tests:
  - N-1 failures → still closed
  - N failures → opens
  - Cooldown expires → half-open
  - Half-open success → closed
  - Half-open failure → re-opens
- Integration test: mock provider that fails deterministically, verify circuit transitions

**Task 6.1.3 — Provider router**
- Implement `cina/orchestration/routing/provider_router.py`
- Health-aware selection: checks circuit breaker state for each provider
- Primary/fallback ordering from configuration
- Metrics: `cina_provider_request_total`, `cina_provider_fallback_total`
- Integration test: simulate primary down, verify queries route to fallback

**Task 6.1.4 — Concurrent timeout fallback**
- Implement `cina/orchestration/routing/fallback.py`
- TTFT monitoring: if primary doesn't produce first token within threshold, race fallback
- Cancellation of losing request
- Metrics: `cina_provider_latency_seconds`
- Integration test: mock primary with 10s delay, verify fallback responds within threshold
- Write `docs/adr/ADR-005-provider-fallback.md` referencing FuelSense ADR-3 pattern evolution

#### Days 19–20: Cache, Rate Limiting & Cost Tracking

**Task 6.2.1 — LSH implementation**
- Implement `cina/orchestration/cache/lsh.py`
- Random hyperplane generation and persistence to Redis
- Binary hash computation from embedding vector
- Configurable number of hyperplanes
- Unit tests:
  - Same vector → same hash (deterministic)
  - Very similar vectors (cosine > 0.99) → same hash (high probability)
  - Dissimilar vectors → different hash (high probability)
  - Hyperplane count affects collision rate

**Task 6.2.2 — Semantic cache**
- Implement `cina/orchestration/cache/semantic_cache.py`
- `lookup`: LSH hash → Redis GET → cosine similarity verification → return cached response or None
- `store`: LSH hash → Redis SETEX with TTL, tagged with prompt version
- `invalidate_version`: cursor-based scan for entries matching a prompt version, delete
- Metrics: `cina_cache_hit_total`, `cina_cache_miss_total`
- Integration test: store a response, query with same embedding → hit; query with different embedding → miss; query with similar embedding (cosine > threshold) → hit
- Write `docs/adr/ADR-004-semantic-cache.md` documenting LSH choice over second vector index

**Task 6.2.3 — Rate limiter**
- Implement `cina/orchestration/limits/rate_limiter.py`
- Redis sorted set sliding window (Architecture Document § 7.6)
- Request-based and token-based limits (configurable per-tenant)
- Returns `RateLimitResult` with `allowed`, `limit`, `remaining`, `reset_at`
- Implement `cina/api/middleware/rate_limit.py`: inject rate limit headers into responses
- Unit tests: simulate burst of requests, verify rejection after limit
- Integration test: send requests above rate limit, verify 429 responses with correct headers

**Task 6.2.4 — Cost tracker**
- Implement `cina/orchestration/limits/cost_tracker.py`
- Log `CostEvent` for every LLM call (input tokens, output tokens, estimated cost, cache hit flag)
- Implement `cina/db/repositories/cost_event.py`: async insert
- Metrics: `cina_cost_usd_total{provider, tenant}`
- Integration test: make 10 queries, verify 10 cost events logged with correct token counts

**Task 6.2.5 — Prompt version router**
- Implement `cina/orchestration/routing/prompt_router.py`
- Weighted random selection from active prompt versions
- Implement `cina/db/repositories/prompt_version.py`: CRUD for prompt versions
- CLI: `cina prompt create --id v1.0 --file prompts/v1.0.txt --weight 1.0 --active`
- Integration test: create two versions with 50/50 weights, run 100 queries, verify ~50/50 split in query logs

#### Days 21–22: Middleware Assembly, Observability & Load Testing

**Task 6.3.1 — Middleware composition**
- Implement `cina/orchestration/middleware.py`: `compose()` function
- Wire together: rate limiter → semantic cache → provider router → fallback handler → cost tracker
- Integrate into query pipeline (replace Phase 2 direct provider call)
- Verify: query endpoint works end-to-end through full middleware stack

**Task 6.3.2 — API key authentication**
- Implement `cina/api/middleware/auth.py`: validate `Authorization: Bearer` header
- Implement `cina/db/repositories/apikey.py`: bcrypt hash storage, tenant mapping
- Implement `cina/cli/apikey.py`: `cina apikey create --tenant demo`, `cina apikey revoke`
- Create a default dev key via CLI
- Verify: requests without valid key get 401, requests with valid key proceed

**Task 6.3.3 — Query logging**
- Implement `cina/db/repositories/query_log.py`: async insert
- Log all query metadata: text, embedding, prompt version, provider, latencies, cache hit, chunks retrieved/used
- Fire-and-forget via `asyncio.create_task` (don't block the response)
- Verify: after 10 queries, 10 rows in `query_logs` with correct data

**Task 6.3.4 — Prometheus metric instrumentation**
- Wire all defined metrics to actual code paths (counters, histograms across all components)
- Verify: after running 10 queries, Prometheus scrape shows non-zero values for all key metrics

**Task 6.3.5 — Grafana dashboards**
- Create 4 dashboards as JSON (Architecture Document § 8.3):
  - Ingestion Pipeline
  - Query Performance
  - Orchestration Health
  - Cost Tracking
- Commit JSON exports to `infra/grafana/dashboards/`
- Grafana provisioning auto-loads them on startup
- Verify: dashboards show live data after running queries

**Task 6.3.6 — DLQ management CLI**
- Implement `cina/cli/dlq.py`: `cina dlq list`, `cina dlq retry --id`, `cina dlq purge`
- Integration test: manually dead-letter a message, list it, retry it, verify it re-enters main queue

**Task 6.3.7 — Load testing**
- Install `locust` (already in dependencies)
- Create `tests/load/locustfile.py`:
  - 10 concurrent users, 1 query/second each, 5 minutes
  - Mix of unique queries and repeated queries (to exercise cache)
  - Record p50, p95, p99 latencies
- Run against full local stack (Docker Compose + `cina serve` on CPU)
- Capture Grafana dashboard screenshots under load
- Save screenshots in `docs/screenshots/`

**Task 6.3.8 — Provider failover load test**
- Extend locustfile or create separate scenario
- Simulate primary provider failure mid-test (mock endpoint or environment variable toggle)
- Verify: queries succeed via fallback, circuit breaker opens, Grafana shows fallback counter spike
- Screenshot the circuit breaker state transition in Grafana

### 6.2 Exit Criteria

- [ ] Provider abstraction: OpenAI and Anthropic both stream through unified interface
- [ ] Circuit breaker opens after N failures, recovers after cooldown
- [ ] Concurrent timeout fallback demonstrated (slow primary → fast fallback)
- [ ] Semantic cache: > 50% hit rate on repeated semantically similar queries
- [ ] Rate limiter: returns 429 with correct headers when limit exceeded
- [ ] Cost tracking: every LLM call logged with token counts and estimated cost
- [ ] Prompt versioning: A/B routing works with weighted selection
- [ ] API key authentication: 401 on missing/invalid key
- [ ] All Prometheus metrics non-zero after query workload
- [ ] 4 Grafana dashboards auto-loaded and showing live data
- [ ] Load test completed, screenshots captured
- [ ] Provider failover demonstrated under load
- [ ] ADR-4 (semantic cache) and ADR-5 (provider fallback) written
- [ ] All unit, integration, and load tests passing

---

## 7. Phase 4: AWS Deployment Proof

**Machine:** Light (CPU) + AWS account
**Duration:** ~3 days
**Goal:** Terraform-provisioned AWS infrastructure, end-to-end demo on live AWS, recorded evidence, clean teardown.

### 7.1 Task Breakdown

#### Day 23: Terraform Modules

**Task 7.1.1 — VPC module**
- `infra/terraform/modules/vpc/`: VPC, public/private subnets across 2 AZs, NAT gateway, route tables, security groups
- Variables: CIDR blocks, AZ selection
- Outputs: VPC ID, subnet IDs, security group IDs

**Task 7.1.2 — RDS module**
- `infra/terraform/modules/rds/`: PostgreSQL 16 on `db.t3.micro`, 20 GB gp3
- pgvector extension enabled via parameter group or post-provisioning script
- Private subnet placement, security group allowing ECS access only
- Variables: instance class, storage, credentials (via variables, not hardcoded)
- Outputs: endpoint, port

**Task 7.1.3 — ElastiCache module**
- `infra/terraform/modules/elasticache/`: Redis 7 on `cache.t3.micro`
- Single-node (no cluster for demo)
- Private subnet, security group
- Outputs: endpoint, port

**Task 7.1.4 — SQS module**
- `infra/terraform/modules/sqs/`: ingestion queue + dead-letter queue
- DLQ policy: maxReceiveCount = 3
- Outputs: queue URLs, ARNs

**Task 7.1.5 — S3 module**
- `infra/terraform/modules/s3/`: single bucket for raw document archival
- Versioning disabled (demo only), lifecycle rule to expire objects after 7 days
- Outputs: bucket name, ARN

**Task 7.1.6 — IAM module**
- `infra/terraform/modules/iam/`: ECS task execution role, task role
- Task role policies: SQS read/write, S3 read/write, RDS connect, ElastiCache connect
- Least privilege: no admin access, no wildcard resources
- Outputs: role ARNs

**Task 7.1.7 — ECS module**
- `infra/terraform/modules/ecs/`: Fargate cluster, task definitions, services
- Query service: 2 tasks, 0.5 vCPU, 1 GB RAM, ALB target group
- Ingestion worker: 1 task, 0.25 vCPU, 0.5 GB RAM, no ALB
- Container definitions referencing ECR images
- Environment variables injected from SSM Parameter Store or Secrets Manager
- Outputs: service names, ALB DNS

**Task 7.1.8 — Root module assembly**
- `infra/terraform/main.tf`: wires all modules together
- `variables.tf`: all configurable inputs with sensible defaults
- `outputs.tf`: ALB endpoint, RDS endpoint, SQS URLs
- `terraform.tfvars.example`: documented example values
- Verify: `terraform init && terraform validate && terraform plan` succeeds

#### Day 24: SQS Queue Implementation & Containerization

**Task 7.2.1 — SQS queue implementation**
- Implement `cina/ingestion/queue/sqs.py`
- `enqueue`: `sqs.send_message`
- `dequeue`: `sqs.receive_message` with long polling
- `acknowledge`: `sqs.delete_message`
- `dead_letter`: built into SQS DLQ policy (automatic after maxReceiveCount)
- Uses `aioboto3` for async AWS SDK access
- Integration test: send and receive messages via SQS (requires AWS credentials)

**Task 7.2.2 — Dockerfiles**
- `Dockerfile.query`: multi-stage build for query service
  - Stage 1: install dependencies with `uv`
  - Stage 2: slim runtime image with only production code
  - Entrypoint: `uvicorn cina.api:app --host 0.0.0.0 --port 8000`
- `Dockerfile.ingestion`: similar, entrypoint is the ingestion worker
- `Dockerfile` for local development (includes both entry points, dev dependencies)
- Verify: `docker build -f Dockerfile.query -t cina-query .` succeeds, image runs locally

**Task 7.2.3 — ECR push script**
- Script to build and push images to ECR:
  ```bash
  #!/bin/bash
  aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_REGISTRY
  docker build -f Dockerfile.query -t $ECR_REGISTRY/cina-query:latest .
  docker push $ECR_REGISTRY/cina-query:latest
  docker build -f Dockerfile.ingestion -t $ECR_REGISTRY/cina-ingestion:latest .
  docker push $ECR_REGISTRY/cina-ingestion:latest
  ```

#### Day 25: Live Demo & Recording

**Task 7.3.1 — Terraform apply**
- Install `asciinema` (or prepare screen recording)
- Start recording
- Run `terraform plan` — save plan output to `docs/terraform/plan.txt`
- Run `terraform apply` — record full provisioning
- Verify all resources created: RDS, ElastiCache, SQS, S3, ECS, ALB

**Task 7.3.2 — Database migration on RDS**
- Connect to RDS via bastion or ECS exec
- Run migrations: create pgvector extension, create all tables and indexes
- Verify: tables exist, pgvector extension loaded

**Task 7.3.3 — Ingestion demo**
- Upload sample data to S3
- Trigger ingestion worker via ECS task
- Monitor SQS queue depth (CloudWatch)
- Verify: documents processed, chunks in RDS pgvector

**Task 7.3.4 — Query demo**
- Send clinical query to ALB endpoint
- Capture streaming SSE response in terminal
- Demonstrate: metadata → tokens → citations → metrics → done event flow
- Show latency numbers in the metrics event

**Task 7.3.5 — Grafana screenshots**
- If Grafana is deployed (optional on AWS — may use CloudWatch instead)
- Or: show CloudWatch metrics for ECS CPU/memory, RDS connections, SQS message counts
- Capture screenshots of dashboards under demo load

**Task 7.3.6 — Terraform destroy**
- Run `terraform destroy` — record clean teardown
- Verify: all resources deleted, no lingering charges
- Stop recording

**Task 7.3.7 — Package demo artifacts**
- Save recording to `docs/demo/` (asciinema `.cast` file or video)
- Save `terraform plan` output to `docs/terraform/plan.txt`
- Save Grafana/CloudWatch screenshots to `docs/screenshots/`
- Document estimated demo cost in `docs/terraform/cost.md`
- Write `docs/adr/ADR-001-queue-abstraction.md` documenting the Redis/SQS protocol pattern with evidence from the demo

### 7.2 Exit Criteria

- [ ] All Terraform modules validated: `terraform validate` passes
- [ ] `terraform plan` output saved as artifact
- [ ] `terraform apply` provisions all infrastructure (recorded)
- [ ] SQS queue implementation works end-to-end
- [ ] Ingestion runs on ECS via SQS
- [ ] Query endpoint responds via ALB with streaming SSE
- [ ] `terraform destroy` cleans up all resources (recorded)
- [ ] Demo recording is 3–5 minutes, shows full lifecycle
- [ ] Cost documented: demo cycle cost and estimated monthly sustained cost
- [ ] ADR-1 (queue abstraction) written with evidence from both local and AWS paths

---

## 8. Phase 5: Documentation & Portfolio Polish

**Machine:** Light (CPU)
**Duration:** ~3 days
**Goal:** README, API reference, architecture diagram, all ADRs finalized, portfolio coherence verified.

### 8.1 Task Breakdown

#### Day 26: README & API Documentation

**Task 8.1.1 — README.md**
- Project overview (2 paragraphs — what it is, why it exists in the portfolio)
- Architecture diagram (Mermaid or ASCII — the three-layer diagram from Architecture Document § 3.1)
- Portfolio context table (the matrix from PRD § 1)
- Tech stack with one-line justifications
- Quickstart:
  - Prerequisites (Docker, Python 3.12, API keys)
  - `docker compose up -d`
  - `cina db migrate`
  - `cina ingest --source pubmed --path ./data/sample/`
  - `cina serve`
  - `curl` example with expected SSE output
- Link to demo recording
- Link to ADRs
- Link to API reference
- Link to Grafana dashboards

**Task 8.1.2 — API reference**
- `docs/api.md`
- Full OpenAPI-style documentation for all endpoints:
  - `POST /v1/query`: request schema, response event types, example curl, error responses
  - `GET /health`: response schema
  - `GET /ready`: response schema, dependency checks
- Authentication section: API key format, header format, error responses
- Rate limiting section: headers, behavior on limit exceeded
- SSE protocol section: full event schema with examples

**Task 8.1.3 — Configuration reference**
- `docs/configuration.md`
- Full `cina.yaml` with comments explaining every parameter
- Environment variable reference table
- Docker Compose environment variable example

#### Day 27: ADR Finalization & Benchmarks

**Task 8.2.1 — Finalize all ADRs**
- Review and polish:
  - `docs/adr/ADR-001-queue-abstraction.md` (written in Phase 4)
  - `docs/adr/ADR-002-hybrid-search.md` (written in Phase 2)
  - `docs/adr/ADR-003-reranking-approach.md` (written in Phase 2)
  - `docs/adr/ADR-004-semantic-cache.md` (written in Phase 3)
  - `docs/adr/ADR-005-provider-fallback.md` (written in Phase 3)
  - `docs/adr/ADR-006-chunking-strategy.md` (written in Phase 1)
- Each ADR must have: Context, Decision, Consequences, and Evidence (benchmark data or implementation proof)
- Cross-reference ADR-5 with FuelSense ADR-3 explicitly

**Task 8.2.2 — Benchmark summary**
- `docs/benchmarks/README.md`
- Summary table of all benchmarks:
  - Chunking: structure-aware vs. fixed-window precision/recall
  - Hybrid search: vector-only vs. BM25-only vs. hybrid
  - Re-ranking: RRF-only vs. cross-encoder quality/latency tradeoff
  - Query latency: p50/p95/p99 by stage
  - Cache hit rate under repeated query workload
  - Ingestion throughput
- Link to detailed benchmark files

#### Day 28: Final Polish & Portfolio Coherence

**Task 8.3.1 — Code cleanup**
- Run `ruff` and `mypy` on entire codebase
- Remove any TODO comments (resolve or document as known limitations)
- Verify all docstrings on public interfaces
- Verify type annotations on all function signatures
- Verify no unused imports, no dead code

**Task 8.3.2 — Test suite verification**
- Run full test suite: `make test && make test-integration`
- Verify all tests pass
- Check test coverage (aim for > 80% on core logic: chunking, RRF, budget, LSH, rate limiter)
- Document any skipped tests with reasons

**Task 8.3.3 — Portfolio coherence review**
- Review all three projects (FuelSense, AgriSense, CINA) as a hiring manager would:
  - Does each project demonstrate a distinct pattern?
  - Are there accidental overlaps?
  - Does the portfolio tell a story of increasing sophistication?
  - Does ADR-5 (CINA fallback) explicitly reference ADR-3 (FuelSense fallback)?
- Update project READMEs if needed to ensure cross-referencing

**Task 8.3.4 — Known limitations document**
- `docs/LIMITATIONS.md`
- Honest assessment of what CINA doesn't handle:
  - Cross-encoder is single-threaded (scaling limitation)
  - No document-level access control
  - No HIPAA compliance
  - Semantic cache has false-negative rate dependent on LSH parameters
  - No automated prompt quality evaluation
  - BM25 uses PostgreSQL built-in FTS, not a dedicated search engine (Elasticsearch would scale better)
- Each limitation with a note on what the production-grade solution would be

### 8.2 Exit Criteria

- [ ] README is clear, complete, and a reviewer can go from clone to running query in under 10 minutes
- [ ] API reference covers all endpoints with examples
- [ ] All 6 ADRs finalized with evidence
- [ ] Benchmark summary table complete
- [ ] `make lint && make typecheck` clean
- [ ] Full test suite passes
- [ ] Known limitations documented honestly
- [ ] Portfolio coherence verified across all three projects
- [ ] Demo recording linked from README

---

## 9. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Bedrock embedding throttling slows ingestion** | Low | Medium | Token bucket rate limiter in embedding provider. Batch size tuning. Bedrock's default TPM quota is significantly higher than OpenAI's, and can be increased via AWS Service Quotas. Ingestion runs overnight if needed as fallback. |
| **PubMed XML format variations break parser** | High | Low | Defensive parsing with `lxml`: missing sections logged and skipped, malformed XML caught at document level. Never fail the pipeline on a single document. Run parser on 100 documents early to catch variations. |
| **Cross-encoder model too slow on target GPU** | Low | Medium | MiniLM-L-6 is tiny (~22M params). Even on RTX 3090, 20 candidates takes ~50ms. Risk is only if GPU memory is contended. Mitigation: dedicated GPU, no other workloads. |
| **pgvector HNSW recall too low at demo scale** | Low | Medium | 200k chunks is well within pgvector's comfort zone. Tune `ef_construction` (200) and `ef_search` (100). If recall is poor, increase `ef_search` at the cost of latency — document the tradeoff. |
| **LLM API costs higher than projected** | Medium | Low | Cost caps in configuration. Semantic cache reduces repeated query costs. Use cheaper models (GPT-4o-mini) for development, full models only for benchmarks and demo. |
| **Terraform provisioning failures** | Medium | Medium | Use well-known module patterns. Test each module independently. `terraform plan` before every `apply`. Budget 1 extra day for AWS debugging. |
| **Vast.ai instance instability** | Medium | High | Commit and push frequently. Keep database dumps as backup artifacts. All state is reproducible from code + data. |
| **FDA/ClinicalTrials API changes** | Low | Low | Bulk download fallback for all sources. Local data cached in `./data/`. API is only needed for initial acquisition. |

---

## 10. API Cost Projections

### 10.1 Embedding Costs (One-Time Ingestion)

Using Amazon Titan Embeddings V2 via Bedrock at ~$0.00002 per 1K input tokens (as of March 2026):

| Corpus | Documents | Est. Chunks | Est. Tokens | Est. Cost |
|--------|-----------|-------------|-------------|-----------|
| PubMed (3,000 articles) | 3,000 | ~30,000 | ~15M | ~$0.30 |
| FDA (750 labels) | 750 | ~7,500 | ~3.75M | ~$0.08 |
| ClinicalTrials (1,500 records) | 1,500 | ~7,500 | ~3.75M | ~$0.08 |
| **Total** | **5,250** | **~45,000** | **~22.5M** | **~$0.45** |

Re-ingestion for benchmarks (chunking benchmark uses 200 articles × 2 configurations):
- Additional ~2,000 chunks × 2 = ~2M tokens = ~$0.04

**Total embedding cost: ~$0.49**

This is ~6x cheaper than the equivalent OpenAI `text-embedding-3-large` cost (~$3.20 for the same corpus). The savings are a direct consequence of using Bedrock, documented as a deliberate infrastructure decision.

### 10.2 LLM Inference Costs (Development & Benchmarking)

Estimated query volume during development:

| Phase | Queries | Avg Input Tokens | Avg Output Tokens | Provider | Est. Cost |
|-------|---------|-----------------|-------------------|----------|-----------|
| Phase 2 (development) | ~200 | 5,000 | 800 | Anthropic (Sonnet) | ~$3.50 |
| Phase 2 (benchmarks) | ~100 | 5,000 | 800 | Anthropic (Sonnet) | ~$1.75 |
| Phase 3 (integration) | ~300 | 5,000 | 800 | Mixed | ~$5.25 |
| Phase 3 (load test) | ~500 | 5,000 | 800 | Mixed | ~$8.75 |
| Phase 4 (AWS demo) | ~50 | 5,000 | 800 | Anthropic (Sonnet) | ~$0.88 |
| **Total** | **~1,150** | | | | **~$20.13** |

Note: using GPT-4o-mini for development queries where response quality doesn't matter (testing streaming, middleware) reduces this significantly. Budget $10 for GPT-4o-mini development and $10 for full-model benchmarks.

### 10.3 AWS Infrastructure Costs (Phase 4 Demo)

Single apply/demo/destroy cycle (~2–3 hours):

| Resource | Hourly Cost | Hours | Cost |
|----------|------------|-------|------|
| ECS Fargate (3 tasks) | ~$0.06 | 3 | $0.18 |
| RDS db.t3.micro | ~$0.018 | 3 | $0.05 |
| ElastiCache t3.micro | ~$0.017 | 3 | $0.05 |
| ALB | ~$0.022 | 3 | $0.07 |
| NAT Gateway | ~$0.045 | 3 | $0.14 |
| Data transfer | — | — | ~$0.10 |
| **Total demo cycle** | | | **~$0.59** |

Budget $5–10 for 2–3 demo attempts (debugging, re-recording).

### 10.4 Total Cost Summary

| Category | Estimated Cost |
|----------|---------------|
| Embedding generation (Bedrock) | ~$0.49 |
| LLM inference (development + benchmarks) | ~$20.00 |
| AWS demo cycles | ~$10.00 |
| Vast.ai — Light machine (~22 days) | ~$33–66 |
| Vast.ai — Heavy machine (~8 days) | ~$32–80 |
| **Total project cost** | **~$95–176** |

The Vast.ai costs dominate. LLM API costs are modest. Embedding costs are negligible thanks to Bedrock pricing. AWS demo cost is minimal.

---

## 11. Milestone Checklist

This is the master checklist. Each item maps to a task in the roadmap. Use this for daily standup tracking.

### Pre-Setup
- [ ] Repository initialized with `pyproject.toml` and lockfile
- [ ] Full package skeleton created (all modules, all `__init__.py`)
- [ ] Protocol stubs in place
- [ ] Ruff + mypy + pytest configured
- [ ] Pre-commit hooks installed
- [ ] Configuration system implemented (YAML + env vars)
- [ ] Docker Compose running (PostgreSQL + pgvector, Redis, Prometheus, Grafana)
- [ ] Database migrations applied (full schema)
- [ ] asyncpg connection pool working
- [ ] Observability skeleton (metrics defined, structured logging, correlation IDs)
- [ ] CLI skeleton with all subcommands

### Phase 1: Ingestion
- [ ] Sample data downloaded (PubMed, FDA, ClinicalTrials)
- [ ] Data models implemented (`Document`, `Section`, `Chunk`)
- [ ] PubMed connector (parse JATS XML → Document)
- [ ] FDA connector (parse SPL XML → Document)
- [ ] ClinicalTrials connector (parse JSON → Document)
- [ ] Sentence splitter with medical abbreviation handling
- [ ] Two-pass chunking engine (structure-aware + sliding window)
- [ ] Embedding provider protocol + Bedrock Titan V2 implementation (batched, rate-limited, retry logic)
- [ ] Embedding worker (queue consumer, idempotent)
- [ ] Redis Streams queue implementation
- [ ] Document repository (upsert, lookup)
- [ ] Chunk repository (bulk upsert, vector search, BM25 search)
- [ ] Ingestion pipeline orchestrator
- [ ] CLI `cina ingest` command with progress bar
- [ ] End-to-end ingestion integration test
- [ ] Chunking benchmark completed
- [ ] ADR-6 written with benchmark results

### Phase 2: Query Serving
- [ ] GPU machine set up and verified
- [ ] Phase 1 database restored on GPU machine
- [ ] Vector search implementation
- [ ] BM25 search implementation
- [ ] Reciprocal Rank Fusion
- [ ] Hybrid search orchestrator (concurrent vector + BM25)
- [ ] Cross-encoder re-ranker (GPU, async via executor)
- [ ] Cross-encoder latency profiling (L-6 vs L-12, various candidate counts)
- [ ] Token budget context assembler (greedy skip-and-try)
- [ ] Prompt construction
- [ ] SSE event formatting
- [ ] LLM provider stub (Anthropic, streaming)
- [ ] Query pipeline orchestrator (full serving path)
- [ ] FastAPI query endpoint (`POST /v1/query`)
- [ ] Health and readiness endpoints
- [ ] CLI `cina serve` command
- [ ] End-to-end query integration test (SSE stream verified)
- [ ] Re-ranking quality benchmark (RRF vs cross-encoder)
- [ ] Hybrid search effectiveness benchmark
- [ ] Query latency profiling (p50/p95/p99 by stage)
- [ ] ADR-2 written (hybrid search)
- [ ] ADR-3 written (re-ranking)

### Phase 3: Orchestration
- [ ] OpenAI provider (full streaming implementation)
- [ ] Anthropic provider (upgraded from stub)
- [ ] Circuit breaker (Redis-backed state machine)
- [ ] Provider router (health-aware selection)
- [ ] Concurrent timeout fallback
- [ ] LSH implementation
- [ ] Semantic cache (lookup, store, invalidation)
- [ ] Rate limiter (sliding window, request + token based)
- [ ] Rate limit middleware (headers on responses)
- [ ] Cost tracker (per-query logging)
- [ ] Prompt version router (weighted A/B)
- [ ] Middleware composition (full orchestration stack)
- [ ] API key authentication
- [ ] Query logging (fire-and-forget async)
- [ ] Prometheus metrics wired to all code paths
- [ ] 4 Grafana dashboards created and provisioned
- [ ] DLQ management CLI
- [ ] Load test (10 users, 5 minutes)
- [ ] Provider failover load test
- [ ] Dashboard screenshots captured
- [ ] ADR-4 written (semantic cache)
- [ ] ADR-5 written (provider fallback)

### Phase 4: AWS Deployment
- [ ] Terraform VPC module
- [ ] Terraform RDS module (pgvector)
- [ ] Terraform ElastiCache module
- [ ] Terraform SQS module (+ DLQ)
- [ ] Terraform S3 module
- [ ] Terraform IAM module
- [ ] Terraform ECS module (query + ingestion)
- [ ] Root module assembly — `terraform validate && terraform plan` passes
- [ ] SQS queue implementation
- [ ] Dockerfiles (query service, ingestion worker)
- [ ] ECR push script
- [ ] `terraform apply` — recorded
- [ ] Database migration on RDS
- [ ] Ingestion demo on live AWS
- [ ] Query demo on live AWS (SSE streaming via ALB)
- [ ] Dashboard/metrics screenshots
- [ ] `terraform destroy` — recorded
- [ ] Demo recording packaged (3–5 minutes)
- [ ] `terraform plan` output saved
- [ ] Cost documented
- [ ] ADR-1 written (queue abstraction)

### Phase 5: Polish
- [ ] README.md complete (quickstart, architecture diagram, links)
- [ ] API reference (`docs/api.md`)
- [ ] Configuration reference (`docs/configuration.md`)
- [ ] All 6 ADRs finalized with evidence
- [ ] Benchmark summary table
- [ ] Code cleanup (lint, typecheck, docstrings, type annotations)
- [ ] Full test suite passing
- [ ] Test coverage > 80% on core logic
- [ ] Known limitations document
- [ ] Portfolio coherence review (cross-reference FuelSense/AgriSense)
- [ ] Demo recording linked from README
- [ ] Final commit — project complete
