"""Benchmark structure-aware versus naive chunking quality metrics."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cina.ingestion.chunking.config import ChunkConfig
from cina.ingestion.chunking.engine import ChunkingEngine
from cina.ingestion.connectors.protocol import RawDocument
from cina.ingestion.connectors.pubmed import PubMedConnector

if TYPE_CHECKING:
    from cina.models.document import Document

_TERM_MIN_LEN = 2
_RELEVANT_TERM_DIVISOR = 2

QUERIES = [
    "cardiology myocardial infarction",
    "oncology tumor metastasis",
    "infectious disease viral infection",
    "endocrinology diabetes insulin",
    "hypertension blood pressure",
    "heart failure ventricular",
    "cancer immunotherapy",
    "renal kidney disease",
    "pneumonia respiratory infection",
    "clinical trial adverse events",
    "contraindications dosage",
    "drug interactions safety",
    "inflammation cytokine",
    "survival mortality outcomes",
    "randomized controlled trial",
    "placebo efficacy",
    "metabolic syndrome obesity",
    "antibiotic resistance",
    "stroke ischemic",
    "arrhythmia atrial fibrillation",
    "autoimmune disease",
    "biomarker sensitivity specificity",
    "risk factor prevalence",
    "therapy response",
    "meta analysis systematic review",
    "adverse reactions",
    "contraindication warning",
    "eligibility criteria",
    "intervention outcomes",
    "results summary",
]

WORD_RE = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> list[str]:
    """Extract normalized query terms used by heuristic relevance scoring."""
    return [token for token in WORD_RE.findall(text.lower()) if len(token) > _TERM_MIN_LEN]


def _score_text(query_terms: list[str], text: str) -> int:
    """Count how many query terms occur in a candidate text."""
    if not text:
        return 0
    lowered = text.lower()
    return sum(1 for token in query_terms if token in lowered)


def _evaluate(
    docs: list[tuple[str, str, list[str]]],
) -> tuple[float, float, int]:
    """Compute precision@10 and recall@10 for static clinical query set."""
    precision_scores: list[float] = []
    recall_scores: list[float] = []

    for query in QUERIES:
        query_terms = _terms(query)
        if not query_terms:
            continue

        relevant = {
            source_id
            for source_id, full_text, _ in docs
            if sum(1 for term in query_terms if term in full_text)
            >= max(1, len(query_terms) // _RELEVANT_TERM_DIVISOR)
        }
        if not relevant:
            continue

        scored: list[tuple[str, int]] = []
        for source_id, _, chunks in docs:
            best = max((_score_text(query_terms, chunk) for chunk in chunks), default=0)
            if best > 0:
                scored.append((source_id, best))

        scored.sort(key=lambda item: item[1], reverse=True)
        top10 = [source_id for source_id, _ in scored[:10]]
        hits = sum(1 for source_id in top10 if source_id in relevant)

        precision_scores.append(hits / 10.0)
        recall_scores.append(hits / len(relevant))

    if not precision_scores:
        return 0.0, 0.0, 0

    return (
        sum(precision_scores) / len(precision_scores),
        sum(recall_scores) / len(recall_scores),
        len(precision_scores),
    )


def _load_pubmed_documents(data_dir: Path, limit: int) -> list[tuple[Document, str]]:
    """Load and parse PubMed XML files into normalized document tuples."""
    connector = PubMedConnector()
    corpus: list[tuple[Document, str]] = []
    for file_path in sorted(data_dir.glob("*.xml"))[:limit]:
        raw = RawDocument(
            source_id=file_path.stem,
            payload=file_path.read_text(encoding="utf-8", errors="ignore"),
            metadata={"path": str(file_path)},
        )
        doc: Document | None = None
        try:
            doc = connector.parse(raw)
        except (ValueError, TypeError, RuntimeError):
            doc = None
        if doc is None:
            continue
        full_text = " ".join(section.content for section in doc.sections).lower()
        corpus.append((doc, full_text))
    return corpus


def _echo(message: str) -> None:
    """Write progress output to stdout for script-oriented execution."""
    sys.stdout.write(f"{message}\n")


def main() -> None:
    """Run chunking benchmark and print aggregate evaluation metrics."""
    parser = argparse.ArgumentParser(description="Benchmark structure-aware vs naive chunking")
    parser.add_argument(
        "--data-dir",
        default="data/pubmed",
        help="Directory containing PubMed XML files",
    )
    parser.add_argument("--limit", type=int, default=200, help="Number of documents to evaluate")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    corpus = _load_pubmed_documents(data_dir, args.limit)
    if not corpus:
        _echo("No parsable documents found")
        return

    structure_engine = ChunkingEngine(
        ChunkConfig(max_chunk_tokens=512, overlap_tokens=64, sentence_boundary_alignment=True),
    )
    naive_engine = ChunkingEngine(
        ChunkConfig(max_chunk_tokens=512, overlap_tokens=64, sentence_boundary_alignment=False),
    )

    structure_docs: list[tuple[str, str, list[str]]] = []
    naive_docs: list[tuple[str, str, list[str]]] = []

    for doc, full_text in corpus:
        structure_chunks = [
            chunk.content
            for chunk in structure_engine.chunk_document(
                doc,
                embedding_model="text-embedding-3-large",
            )
        ]
        naive_chunks = [
            chunk.content
            for chunk in naive_engine.chunk_document(doc, embedding_model="text-embedding-3-large")
        ]
        structure_docs.append((doc.source_id, full_text, structure_chunks))
        naive_docs.append((doc.source_id, full_text, naive_chunks))

    structure_p10, structure_r10, query_count = _evaluate(structure_docs)
    naive_p10, naive_r10, _ = _evaluate(naive_docs)

    _echo(f"DOCS={len(corpus)}")
    _echo(f"QUERIES_EVAL={query_count}")
    _echo(f"STRUCTURE_P10={structure_p10:.4f}")
    _echo(f"STRUCTURE_R10={structure_r10:.4f}")
    _echo(f"NAIVE_P10={naive_p10:.4f}")
    _echo(f"NAIVE_R10={naive_r10:.4f}")


if __name__ == "__main__":
    main()
