from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .core import BenchmarkMetadata, BenchmarkRun, BenchmarkSample, BenchmarkSummary

STAGE_COLUMNS = (
    "record_ms",
    "transcribe_ms",
    "proposal_ms",
    "response_ms",
    "agent_ms",
    "synthesize_ms",
    "play_ms",
)


def write_metadata(path: Path, metadata: BenchmarkMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(metadata), indent=2, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, samples: list[BenchmarkSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            handle.write(json.dumps(asdict(sample), ensure_ascii=False, default=str))
            handle.write("\n")


def write_csv(path: Path, samples: list[BenchmarkSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "scenario",
        "iteration",
        "is_warmup",
        "status",
        "total_ms",
        "rss_before_bytes",
        "rss_after_bytes",
        "swap_before_bytes",
        "swap_after_bytes",
        *STAGE_COLUMNS,
        "started_at",
        "finished_at",
        "notes",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            row = {
                "scenario": sample.scenario,
                "iteration": sample.iteration,
                "is_warmup": sample.is_warmup,
                "status": sample.status,
                "total_ms": _format_number(sample.total_ms),
                "rss_before_bytes": sample.rss_before_bytes,
                "rss_after_bytes": sample.rss_after_bytes,
                "swap_before_bytes": sample.swap_before_bytes,
                "swap_after_bytes": sample.swap_after_bytes,
                "started_at": sample.started_at,
                "finished_at": sample.finished_at,
                "notes": sample.notes,
                "error": sample.error,
            }
            for column in STAGE_COLUMNS:
                row[column] = _format_number(sample.stage_ms.get(column)) if column in sample.stage_ms else ""
            writer.writerow(row)


def print_summary(run: BenchmarkRun) -> None:
    rows = run.summaries
    if not rows:
        print("No benchmark samples were recorded.")
        return

    title = "scenario"
    print("\n=========================== benchmark summary ===========================")
    print(f"{title:16s}  {'runs':>4s}  {'fail':>4s}  {'mean ms':>10s}  {'p95 ms':>10s}  {'min ms':>10s}  {'max ms':>10s}")
    print("------------------------------------------------------------------------")
    for row in rows:
        print(
            f"{row.scenario:16s}  "
            f"{row.successful_runs:4d}  "
            f"{row.failed_runs:4d}  "
            f"{_fmt(row.mean_ms):>10s}  "
            f"{_fmt(row.p95_ms):>10s}  "
            f"{_fmt(row.min_ms):>10s}  "
            f"{_fmt(row.max_ms):>10s}"
        )
    print("========================================================================")


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _format_number(value: float | None) -> float | str:
    if value is None:
        return ""
    return round(float(value), 6)
