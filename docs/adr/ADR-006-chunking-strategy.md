# ADR-006: Chunking Strategy Benchmark

## Status
Accepted

## Date
2026-03-19

## Context
Phase 1 requires validating structure-aware chunking against a naive fixed-window strategy (ADR-6 objective). The retrieval system uses hybrid search and RRF, but this benchmark focuses specifically on chunking quality proxy metrics over the local PubMed corpus.

## Decision
Use the structure-aware two-pass chunker (`sentence_boundary_alignment=True`) as the default strategy. The benchmark run showed parity with the naive baseline on this proxy setup, so we retain structure-aware chunking due to better semantic boundaries and readability without observable quality regression.

## Method
- Dataset: first 200 local PubMed XML documents from `data/pubmed`
- Query set: 30 medical queries spanning cardiology, oncology, infectious disease, endocrinology, drug safety, and outcomes
- Relevance heuristic: a document is relevant if at least half of query terms appear in the full parsed document text
- Ranking heuristic: for each document, score is max term-overlap across its chunks
- Metrics: precision@10 and recall@10 averaged across evaluated queries

Command:

```bash
/workspace/CINA/.venv/bin/python scripts/benchmark_chunking.py --data-dir data/pubmed --limit 200
```

## Results
- Documents evaluated: 200
- Queries evaluated: 30
- Structure-aware:
  - precision@10: 1.0000
  - recall@10: 0.0935
- Naive fixed-window:
  - precision@10: 1.0000
  - recall@10: 0.0935

## Consequences
- The proxy benchmark did not show measurable ranking differences at top-10 between strategies on this corpus slice.
- Structure-aware chunking remains preferred because it preserves sentence boundaries and section semantics, which is useful for context assembly and citation clarity.
- A future benchmark can replace the heuristic relevance set with manually labeled relevance judgments for higher-fidelity offline evaluation.

## Follow-up
- Add curated relevance labels for a subset of queries to reduce heuristic bias.
- Extend benchmark to full hybrid pipeline (vector + BM25 + RRF + reranker) once serving integration tests are in place.
