"""Benchmark repeated ingestion runs through the HEDIS API.

Example:
    python scripts/benchmark_ingestion.py \
        --input-dir C:/data/synthea/csv \
        --adapter-name synthea_csv \
        --runs 5 \
        --output benchmark.json

The benchmark measures wall time externally and uses the backend's stage
metrics for CSV reading, mapping, validation, and database insertion.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx


TERMINAL_STATUSES = {"completed", "partially_completed", "failed"}


def percentile(values: list[float], percentage: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percentage / 100) * (len(ordered) - 1))))
    return ordered[index]


def start_job(client: httpx.Client, api_url: str, input_dir: str, adapter_name: str) -> str:
    response = client.post(
        f"{api_url}/api/jobs/start",
        params={"input_dir": input_dir, "adapter_name": adapter_name},
    )
    response.raise_for_status()
    return response.json()["job_id"]


def wait_for_job(client: httpx.Client, api_url: str, job_id: str, poll_seconds: float) -> dict[str, Any]:
    while True:
        response = client.get(f"{api_url}/api/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(poll_seconds)


def run_benchmark(api_url: str, input_dir: str, adapter_name: str, runs: int, poll_seconds: float) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=None) as client:
        health = client.get(f"{api_url}/health")
        health.raise_for_status()
        for run_number in range(1, runs + 1):
            started = time.perf_counter()
            job_id = start_job(client, api_url, input_dir, adapter_name)
            job = wait_for_job(client, api_url, job_id, poll_seconds)
            wall_seconds = time.perf_counter() - started
            summary = job.get("summary") or {}
            timings = summary.get("timings") or {}
            source_rows = job.get("total_source_records", 0) or 0
            generated = job.get("resources_generated", 0) or 0
            results.append(
                {
                    "run": run_number,
                    "job_id": job_id,
                    "status": job["status"],
                    "source_rows": source_rows,
                    "resources_generated": generated,
                    "resources_validated": job.get("resources_validated", 0) or 0,
                    "resources_persisted": job.get("resources_persisted", 0) or 0,
                    "errors": job.get("error_count", 0) or 0,
                    "warnings": job.get("warning_count", 0) or 0,
                    "wall_seconds": round(wall_seconds, 3),
                    "csv_reading_seconds": timings.get("csv_reading_seconds", 0.0),
                    "mapping_seconds": timings.get("mapping_seconds", 0.0),
                    "validation_seconds": timings.get("validation_seconds", 0.0),
                    "database_insertion_seconds": timings.get("database_insertion_seconds", 0.0),
                    "resources_per_second": round(generated / wall_seconds, 2) if wall_seconds else 0.0,
                    "rows_per_second": round(source_rows / wall_seconds, 2) if wall_seconds else 0.0,
                }
            )
            print(json.dumps(results[-1]))

    metric_names = [
        "wall_seconds",
        "csv_reading_seconds",
        "mapping_seconds",
        "validation_seconds",
        "database_insertion_seconds",
        "resources_per_second",
        "rows_per_second",
    ]
    aggregate: dict[str, dict[str, float]] = {}
    for metric in metric_names:
        values = [float(result[metric]) for result in results]
        aggregate[metric] = {
            "min": round(min(values), 3) if values else 0.0,
            "mean": round(statistics.mean(values), 3) if values else 0.0,
            "p50": round(percentile(values, 50), 3),
            "p95": round(percentile(values, 95), 3),
            "max": round(max(values), 3) if values else 0.0,
        }

    return {
        "api_url": api_url,
        "input_dir": input_dir,
        "adapter_name": adapter_name,
        "runs": runs,
        "results": results,
        "aggregate": aggregate,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--adapter-name", default="synthea_csv")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.json"))
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    report = run_benchmark(args.api_url.rstrip("/"), args.input_dir, args.adapter_name, args.runs, args.poll_seconds)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Benchmark report written to {args.output}")
    print(json.dumps(report["aggregate"], indent=2))


if __name__ == "__main__":
    main()
