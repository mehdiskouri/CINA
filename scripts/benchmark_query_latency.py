"""Benchmark: end-to-end query latency profiling.

Runs queries against the full serving pipeline and captures per-stage
latency breakdown. Results saved to docs/benchmarks/query_latency.md.

Usage:
    python scripts/benchmark_query_latency.py [--queries N] [--base-url URL]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

import httpx


async def run_query(client: httpx.AsyncClient, url: str, query: str) -> dict[str, float]:
    """Send a query and parse the metrics SSE event for stage latencies."""
    start = time.perf_counter()
    resp = await client.post(
        f"{url}/v1/query",
        json={"query": query},
        headers={"Content-Type": "application/json"},
    )
    total_ms = (time.perf_counter() - start) * 1000

    metrics: dict[str, float] = {"total_ms": total_ms}

    for block in resp.text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        event_type = ""
        data = ""
        for line in lines:
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event_type == "metrics" and data:
            parsed = json.loads(data)
            for key in [
                "search_latency_ms",
                "rerank_latency_ms",
                "assembly_latency_ms",
                "llm_ttft_ms",
                "llm_total_ms",
            ]:
                metrics[key] = float(parsed.get(key, 0))

    # Pre-LLM latency
    metrics["pre_llm_ms"] = (
        metrics.get("search_latency_ms", 0)
        + metrics.get("rerank_latency_ms", 0)
        + metrics.get("assembly_latency_ms", 0)
    )
    return metrics


SAMPLE_QUERIES = [
    "What are the contraindications of metformin?",
    "Describe the mechanism of action of pembrolizumab.",
    "What is the recommended INR range for warfarin therapy?",
    "What are common side effects of lisinopril?",
    "How does dexamethasone help in COVID-19 treatment?",
    "What is the role of BRCA1 in cancer?",
    "What is the target HbA1c for elderly diabetic patients?",
    "Describe dual antiplatelet therapy after PCI.",
    "How does rituximab target CD20?",
    "What is the pediatric dosing for amoxicillin-clavulanate?",
    "How does naloxone reverse opioid overdose?",
    "Explain tamoxifen and CYP2D6 interaction.",
    "What is the management of nivolumab hepatotoxicity?",
    "Why does amlodipine cause ankle edema?",
    "How to prevent cisplatin nephrotoxicity?",
    "What infections are associated with TNF inhibitors?",
    "What are cardiovascular outcomes of semaglutide?",
    "How does idarucizumab reverse dabigatran?",
    "What is the role of folic acid with methotrexate?",
    "What drugs affect levothyroxine absorption?",
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=int, default=len(SAMPLE_QUERIES))
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--runs", type=int, default=3, help="Runs per query")
    args = parser.parse_args()

    queries = (SAMPLE_QUERIES * ((args.queries // len(SAMPLE_QUERIES)) + 1))[: args.queries]
    all_metrics: list[dict[str, float]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        for i, query in enumerate(queries):
            for run in range(args.runs):
                print(f"[{i * args.runs + run + 1}/{len(queries) * args.runs}] {query[:50]}...")
                try:
                    m = await run_query(client, args.base_url, query)
                    all_metrics.append(m)
                except Exception as exc:
                    print(f"  ERROR: {exc}")

    if not all_metrics:
        print("No successful queries. Exiting.")
        return

    # Aggregate
    stages = [
        "search_latency_ms",
        "rerank_latency_ms",
        "assembly_latency_ms",
        "llm_ttft_ms",
        "llm_total_ms",
        "pre_llm_ms",
        "total_ms",
    ]

    os.makedirs("docs/benchmarks", exist_ok=True)
    bench_path = "docs/benchmarks/query_latency.md"
    with open(bench_path, "w") as f:  # noqa: ASYNC230
        f.write("# Query Latency Benchmark\n\n")
        f.write(f"**Queries:** {len(all_metrics)} total runs\n\n")
        f.write("## Per-Stage Latency\n\n")
        f.write("| Stage | p50 (ms) | p95 (ms) | p99 (ms) | Mean (ms) |\n")
        f.write("|-------|---------|---------|---------|----------|\n")
        for stage in stages:
            vals = sorted(m.get(stage, 0) for m in all_metrics)
            if not vals:
                continue
            p50 = vals[len(vals) // 2]
            p95 = vals[int(len(vals) * 0.95)]
            p99 = vals[int(len(vals) * 0.99)]
            mean = statistics.mean(vals)
            label = stage.replace("_ms", "").replace("_", " ").title()
            f.write(f"| {label} | {p50:.1f} | {p95:.1f} | {p99:.1f} | {mean:.1f} |\n")

        f.write("\n## Target Verification\n\n")
        pre_llm = sorted(m.get("pre_llm_ms", 0) for m in all_metrics)
        p95_pre_llm = pre_llm[int(len(pre_llm) * 0.95)]
        total = sorted(m.get("total_ms", 0) for m in all_metrics)
        p95_total = total[int(len(total) * 0.95)]
        f.write(f"- Pre-LLM p95: **{p95_pre_llm:.1f}ms** (target: <500ms)\n")
        f.write(f"- Total p95: **{p95_total:.1f}ms** (target: <3000ms)\n")

    print("\nResults written to docs/benchmarks/query_latency.md")
    print(f"Pre-LLM p95: {p95_pre_llm:.1f}ms")
    print(f"Total p95: {p95_total:.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())
