# CINA

Clinical Index and Narrative Assembly — a RAG backend for clinical literature (PubMed, FDA labels, ClinicalTrials.gov).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Environment

Copy `.env.example` to `env.keys.local` (or `.env.keys.local`) and fill in your API keys:

```bash
cp .env.example env.keys.local
# Edit env.keys.local with your OPENAI_API_KEY and ANTHROPIC_API_KEY
```

Set database and Redis URLs:

```bash
export DATABASE_URL="postgresql://cina:cina_dev@localhost:5432/cina"
export REDIS_URL="redis://localhost:6379/0"
source env.keys.local
```

## Database

```bash
# Run migrations
python -m cina db migrate
```

### Restoring from a dump

If a `cina_db_dump.sql.gz` backup exists (316 MB, contains 98,602 pre-embedded chunks):

```bash
gunzip -c cina_db_dump.sql.gz | pg_restore -U cina -d cina -h localhost --no-owner --clean 2>/dev/null
```

## Phase 1 — Ingestion

```bash
python -m cina ingest run --source clinicaltrials --path data/clinicaltrials
python -m cina ingest run --source pubmed --path data/pubmed
python -m cina ingest run --source fda --path data/fda
```

## Phase 2 — Query Serving

```bash
python -m cina serve --port 8000
```

Test a query:

```bash
curl -N -X POST http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <cina_sk_...>" \
  -d '{"query": "What are the latest treatments for metastatic breast cancer?"}'

For local development without API keys, set `CINA_AUTH_DISABLED=1`.
```

See [docs/PIPELINE_RUN_REPORT.md](docs/PIPELINE_RUN_REPORT.md) for full pipeline documentation and benchmark results.

## Development

```bash
python -m ruff check cina/ tests/ scripts/    # lint
python -m ruff format cina/ tests/ scripts/    # format
python -m mypy cina/ --ignore-missing-imports  # type check
python -m pytest tests/unit/ -v                # unit tests
```

## Phase 4 — AWS Deployment Proof

Build and push container images:

```bash
./scripts/ecr_push.sh
```

Provision AWS infrastructure:

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

Full operational runbook: `docs/terraform/DEPLOYMENT_RUNBOOK.md`.
