CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'source_type') THEN
        CREATE TYPE source_type AS ENUM ('pubmed', 'fda', 'clinicaltrials');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ingestion_status') THEN
        CREATE TYPE ingestion_status AS ENUM ('pending', 'running', 'completed', 'failed');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source source_type NOT NULL,
    status ingestion_status DEFAULT 'pending',
    documents_total INTEGER DEFAULT 0,
    documents_done INTEGER DEFAULT 0,
    chunks_created INTEGER DEFAULT 0,
    errors JSONB DEFAULT '[]',
    config JSONB DEFAULT '{}',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source source_type NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    authors JSONB DEFAULT '[]',
    publication_date DATE,
    raw_metadata JSONB DEFAULT '{}',
    ingestion_id UUID NOT NULL REFERENCES ingestion_jobs(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS sections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_type TEXT NOT NULL,
    heading TEXT,
    content TEXT NOT NULL,
    "order" INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sections_document ON sections(document_id);

CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_id UUID NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    overlap_tokens INTEGER DEFAULT 0,
    embedding vector(512),
    embedding_model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL DEFAULT 512,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (content_hash, embedding_model)
);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 200);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'chunks' AND column_name = 'content_tsvector'
    ) THEN
        EXECUTE 'ALTER TABLE chunks ADD COLUMN content_tsvector tsvector GENERATED ALWAYS AS (to_tsvector(''english'', content)) STORED';
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_chunks_fts ON chunks USING gin(content_tsvector);
CREATE INDEX IF NOT EXISTS idx_documents_title_fts ON documents USING gin(to_tsvector('english', title));

CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    description TEXT,
    traffic_weight REAL DEFAULT 0.0 CHECK (traffic_weight >= 0.0 AND traffic_weight <= 1.0),
    active BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS query_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    query_embedding vector(512),
    prompt_version_id TEXT REFERENCES prompt_versions(id),
    provider_used TEXT NOT NULL,
    fallback_triggered BOOLEAN DEFAULT false,
    cache_hit BOOLEAN DEFAULT false,
    total_latency_ms INTEGER,
    search_latency_ms INTEGER,
    rerank_latency_ms INTEGER,
    llm_latency_ms INTEGER,
    chunks_retrieved INTEGER,
    chunks_used INTEGER,
    tenant_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_query_logs_created ON query_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_query_logs_tenant ON query_logs(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS cost_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_id UUID REFERENCES query_logs(id),
    tenant_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    cache_hit BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cost_events_tenant ON cost_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cost_events_provider ON cost_events(provider, created_at DESC);
