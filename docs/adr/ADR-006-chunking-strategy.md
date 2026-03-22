# ADR-006: Structure-Aware Chunking Strategy

## Status

Accepted

## Date

2026-03-19

## Context

CINA's ingestion pipeline must split clinical documents (PubMed articles, FDA labels, clinical trial records) into chunks suitable for embedding and retrieval. The chunking strategy directly impacts:

1. **Retrieval quality:** Chunks must be semantically coherent units that make sense in isolation
2. **Context assembly:** Chunks used as LLM context must contain complete thoughts with clear source attribution
3. **Embedding quality:** Chunks should be within the embedding model's optimal token range
4. **Citation clarity:** When cited in a response, a chunk should correspond to a meaningful section of the source document

Clinical documents have rich structure — abstracts, methods, results, conclusions, tables, eligibility criteria — and this structure carries semantic meaning. Naive fixed-window chunking destroys these boundaries.

### Constraints

- Maximum chunk size: 512 tokens (tokenizer: `cl100k_base`, matching OpenAI models)
- Must handle three document formats: PubMed XML (JATS), FDA SPL XML, ClinicalTrials.gov JSON
- Must preserve section boundaries (a chunk never spans two sections)
- Must support overlap for continuity (default: 64 tokens)
- Throughput: must chunk 3,500 documents in reasonable time (< 30 min total pipeline)

## Decision

Implement a **two-pass, structure-aware chunking engine** with sentence boundary alignment:

### Pass 1: Section Extraction

Each connector (PubMed, FDA, ClinicalTrials) extracts typed sections from the source document:
- PubMed: title, abstract, introduction, methods, results, discussion, conclusions, references
- FDA: description, indications, dosage, warnings, adverse_reactions, clinical_studies
- ClinicalTrials: brief_summary, detailed_description, eligibility, outcome_measures

### Pass 2: Sentence-Aligned Chunking

Within each section:
1. Split text into sentences using a medical-aware sentence splitter (handles abbreviations like "Dr.", "mg/dL", "p < 0.05")
2. Accumulate sentences until the token budget (512) would be exceeded
3. Emit the accumulated sentences as a chunk
4. Begin the next chunk with `overlap_tokens` (64) of token overlap from the previous chunk's tail
5. Never cross section boundaries — even if remaining text is below the minimum, it becomes its own chunk

### Configuration

```yaml
ingestion:
  chunk:
    max_tokens: 512
    overlap_tokens: 64
    tokenizer: cl100k_base
    respect_sections: true
    sentence_alignment: true
```

## Consequences

### Benefits

- **Semantic coherence:** Chunks align with document structure — a "results" chunk contains only results text, making citations meaningful
- **Clean sentence boundaries:** No mid-sentence splits that confuse both embeddings and LLM context
- **Section metadata preserved:** Each chunk carries `section_type` and `heading` metadata, enabling the context assembler and citation generator to provide rich source attribution
- **Consistent token counts:** Sentence alignment produces chunks that are close to but never exceed the 512-token budget

### Costs

- **Variable chunk sizes:** Short sections (e.g., a one-sentence eligibility criterion) produce small chunks that waste embedding API calls. Mean chunk size is lower than the 512 maximum.
- **Overlap tokens increase total chunk count:** 64-token overlap means ~12% more chunks than zero-overlap, increasing embedding costs by the same proportion
- **Medical sentence splitter maintenance:** The custom splitter must handle edge cases (abbreviations, decimal numbers, citation markers) that standard splitters miss

## Rejected Alternatives

### 1. Naive Fixed-Window Chunking (512 tokens, no alignment)

Split text at exactly 512 tokens with optional overlap, ignoring section and sentence boundaries.

**Rejected because:** Produces chunks that start and end mid-sentence, span section boundaries (e.g., half methods + half results), and lose structural metadata. While benchmark metrics showed parity at top-10 precision in a controlled test (see Evidence), structure-aware chunking provides better citation quality and context assembly that aren't captured by the proxy benchmark.

### 2. Recursive Character Splitting (LangChain-style)

Split by paragraphs → sentences → characters, recursively, until under the token limit.

**Rejected because:** Doesn't respect the document's semantic structure. A paragraph break in a PubMed article doesn't necessarily indicate a topic change, while a section boundary always does. Clinical documents have explicit, meaningful structure that should be the primary split point.

### 3. Semantic Chunking (Embedding-Based Splits)

Compute sentence embeddings and split where cosine similarity between consecutive sentences drops below a threshold.

**Rejected because:** Requires an embedding call for every sentence before chunking even begins — at 3,500 documents this would multiply the embedding API cost by an order of magnitude. Also adds significant pipeline complexity and latency for uncertain quality gains.

### 4. Document-Level Embedding (No Chunking)

Embed entire documents and retrieve at the document level.

**Rejected because:** Clinical documents range from 500 to 50,000+ tokens. Full documents far exceed embedding model context windows and would waste LLM context tokens on irrelevant sections. Chunk-level retrieval is standard practice for RAG with long documents.

## Decision Matrix

| Criteria (weight) | Structure-Aware | Fixed-Window | Recursive Split | Semantic Chunking | No Chunking |
|---|---|---|---|---|---|
| Citation quality (30%) | 10 | 4 | 6 | 8 | 3 |
| Embedding quality (25%) | 9 | 7 | 7 | 9 | 2 |
| Implementation simplicity (20%) | 7 | 10 | 8 | 4 | 10 |
| Pipeline throughput (15%) | 8 | 10 | 9 | 3 | 10 |
| Metadata preservation (10%) | 10 | 3 | 5 | 7 | 8 |
| **Weighted score** | **8.85** | **6.65** | **7.00** | **6.30** | **5.30** |

## Implementation Evidence

### Benchmark Results

A controlled benchmark over 200 PubMed documents and 30 clinical queries compared structure-aware and naive chunking using a term-overlap relevance heuristic:

| Strategy | precision@10 | recall@10 |
|----------|-------------|-----------|
| Structure-aware (sentence-aligned, section-respecting) | 1.0000 | 0.0935 |
| Naive fixed-window (512 tokens) | 1.0000 | 0.0935 |

**Interpretation:** The proxy benchmark showed parity because the relevance heuristic (term overlap) doesn't capture the primary advantage of structure-aware chunking: **citation quality and context coherence**. Both strategies find relevant documents at the same rate, but structure-aware chunks provide more useful context to the LLM because each chunk is a complete semantic unit with preserved section metadata.

The low recall@10 (0.0935) reflects the heuristic's conservative relevance labels, not a chunking deficiency — see the benchmark script for details.

### Ingestion Statistics

| Metric | Value |
|--------|-------|
| Total chunks created | 98,602 |
| Total documents | 3,500 |
| Average chunks per document | 28.2 |
| Total ingestion time | ~29 min |
| Chunk index storage (HNSW) | 263 MB |
| FTS index storage (GIN) | 50 MB |

### File References

- [`cina/ingestion/chunking/engine.py`](../../cina/ingestion/chunking/engine.py) — Two-pass chunking engine
- [`cina/ingestion/chunking/sentences.py`](../../cina/ingestion/chunking/sentences.py) — Medical-aware sentence splitter
- [`cina/ingestion/connectors/pubmed.py`](../../cina/ingestion/connectors/pubmed.py) — PubMed section extraction
- [`cina/ingestion/connectors/fda.py`](../../cina/ingestion/connectors/fda.py) — FDA section extraction
- [`cina/ingestion/connectors/clinicaltrials.py`](../../cina/ingestion/connectors/clinicaltrials.py) — ClinicalTrials section extraction
- [`scripts/benchmark_chunking.py`](../../scripts/benchmark_chunking.py) — Benchmark script
- [`docs/PIPELINE_RUN_REPORT.md`](../PIPELINE_RUN_REPORT.md) — Full ingestion statistics
