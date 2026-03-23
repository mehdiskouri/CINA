"""Benchmark: cross-encoder reranking latency profiling.

Profiles MiniLM-L-6 and MiniLM-L-12 at various candidate counts on GPU and CPU.
Results saved to docs/benchmarks/rerank_latency.md.

Usage:
    python scripts/benchmark_rerank.py
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import torch
from sentence_transformers import CrossEncoder


def _make_pairs(query: str, n: int) -> list[tuple[str, str]]:
    """Generate synthetic query-document pairs for benchmarking."""
    docs = [
        f"Clinical study document {i}: This is a synthetic paragraph about "
        f"pharmacokinetics and drug metabolism for benchmark candidate {i}."
        for i in range(n)
    ]
    return [(query, doc) for doc in docs]


def profile_model(
    model_name: str,
    device: str,
    candidate_counts: list[int],
    n_runs: int = 50,
) -> dict[int, dict[str, float]]:
    """Profile reranking latency percentiles for one model and device."""
    _echo(f"\nLoading {model_name} on {device}...")
    model = CrossEncoder(model_name, device=device)

    query = "What are the contraindications of metformin in patients with renal impairment?"
    results: dict[int, dict[str, float]] = {}

    for n_candidates in candidate_counts:
        pairs = _make_pairs(query, n_candidates)
        latencies: list[float] = []

        # Warmup
        for _ in range(3):
            model.predict(pairs)

        for _ in range(n_runs):
            start = time.perf_counter()
            model.predict(pairs)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        results[n_candidates] = {
            "p50": latencies[len(latencies) // 2],
            "p95": latencies[int(len(latencies) * 0.95)],
            "p99": latencies[int(len(latencies) * 0.99)],
            "mean": statistics.mean(latencies),
        }
        _echo(
            f"  {n_candidates} candidates: "
            f"p50={results[n_candidates]['p50']:.1f}ms "
            f"p95={results[n_candidates]['p95']:.1f}ms "
            f"p99={results[n_candidates]['p99']:.1f}ms",
        )

    return results


def _echo(message: str) -> None:
    """Write progress output to stdout for interactive script runs."""
    sys.stdout.write(f"{message}\n")


def main() -> None:
    """Run reranking latency benchmark and write markdown report."""
    has_cuda = torch.cuda.is_available()
    _echo(f"CUDA available: {has_cuda}")
    if has_cuda:
        _echo(f"GPU: {torch.cuda.get_device_name(0)}")

    all_results: dict[str, dict[int, dict[str, float]]] = {}

    # GPU profiles
    if has_cuda:
        all_results["MiniLM-L-6 (GPU)"] = profile_model(
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "cuda",
            [10, 20, 30, 50],
        )
        all_results["MiniLM-L-12 (GPU)"] = profile_model(
            "cross-encoder/ms-marco-MiniLM-L-12-v2",
            "cuda",
            [10, 20, 30, 50],
        )

    # CPU profiles (always)
    all_results["MiniLM-L-6 (CPU)"] = profile_model(
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "cpu",
        [10, 20],
    )

    # Write results
    output_path = Path("docs/benchmarks/rerank_latency.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write("# Cross-Encoder Reranking Latency Benchmark\n\n")
        f.write(f"**CUDA:** {has_cuda}")
        if has_cuda:
            f.write(f" ({torch.cuda.get_device_name(0)})")
        f.write("\n\n")

        for config_name, results in all_results.items():
            f.write(f"## {config_name}\n\n")
            f.write("| Candidates | p50 (ms) | p95 (ms) | p99 (ms) | Mean (ms) |\n")
            f.write("|-----------|---------|---------|---------|----------|\n")
            for n, stats in sorted(results.items()):
                row = (
                    f"| {n} | {stats['p50']:.1f} | {stats['p95']:.1f} | "
                    f"{stats['p99']:.1f} | {stats['mean']:.1f} |\n"
                )
                f.write(row)
            f.write("\n")

    _echo("\nResults written to docs/benchmarks/rerank_latency.md")


if __name__ == "__main__":
    main()
