# Export generator
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
        "benchmark_process_rss_before_bytes",
        "benchmark_process_rss_after_bytes",
        "benchmark_process_swap_before_bytes",
        "benchmark_process_swap_after_bytes",
        "process_tree_rss_before_bytes",
        "process_tree_rss_after_bytes",
        "process_tree_swap_before_bytes",
        "process_tree_swap_after_bytes",
        "system_ram_used_before_bytes",
        "system_ram_used_after_bytes",
        "system_ram_available_before_bytes",
        "system_ram_available_after_bytes",
        "system_swap_used_before_bytes",
        "system_swap_used_after_bytes",
        "system_swap_available_before_bytes",
        "system_swap_available_after_bytes",
        *STAGE_COLUMNS,
        "started_at",
        "finished_at",
        "telemetry_notes",
        "components_invoked",
        "profile",
        "component",
        "execution_path",
        "lifecycle",
        "success",
        "hardware",
        "runtime_version",
        "pytorch_device",
        "model_precision",
        "batch_size",
        "warmup_iterations",
        "measurement_iterations",
        "initialization_ms",
        "inference_ms",
        "audio_duration_ms",
        "rtf",
        "available_execution_providers",
        "requested_execution_providers",
        "session_execution_providers",
        "provider_selection_status",
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
                "benchmark_process_rss_before_bytes": sample.benchmark_process_rss_before_bytes,
                "benchmark_process_rss_after_bytes": sample.benchmark_process_rss_after_bytes,
                "benchmark_process_swap_before_bytes": sample.benchmark_process_swap_before_bytes,
                "benchmark_process_swap_after_bytes": sample.benchmark_process_swap_after_bytes,
                "process_tree_rss_before_bytes": sample.process_tree_rss_before_bytes,
                "process_tree_rss_after_bytes": sample.process_tree_rss_after_bytes,
                "process_tree_swap_before_bytes": sample.process_tree_swap_before_bytes,
                "process_tree_swap_after_bytes": sample.process_tree_swap_after_bytes,
                "system_ram_used_before_bytes": sample.system_ram_used_before_bytes,
                "system_ram_used_after_bytes": sample.system_ram_used_after_bytes,
                "system_ram_available_before_bytes": sample.system_ram_available_before_bytes,
                "system_ram_available_after_bytes": sample.system_ram_available_after_bytes,
                "system_swap_used_before_bytes": sample.system_swap_used_before_bytes,
                "system_swap_used_after_bytes": sample.system_swap_used_after_bytes,
                "system_swap_available_before_bytes": sample.system_swap_available_before_bytes,
                "system_swap_available_after_bytes": sample.system_swap_available_after_bytes,
                "started_at": sample.started_at,
                "finished_at": sample.finished_at,
                "telemetry_notes": sample.telemetry_notes,
                "components_invoked": _json_cell(sample.details.get("components_invoked")),
                "profile": sample.profile,
                "component": sample.component,
                "execution_path": sample.execution_path,
                "lifecycle": sample.lifecycle,
                "success": sample.success,
                "hardware": sample.hardware,
                "runtime_version": sample.runtime_version,
                "pytorch_device": sample.pytorch_device,
                "model_precision": sample.model_precision,
                "batch_size": sample.batch_size,
                "warmup_iterations": sample.warmup_iterations,
                "measurement_iterations": sample.measurement_iterations,
                "initialization_ms": _format_number(sample.initialization_ms),
                "inference_ms": _format_number(sample.inference_ms),
                "audio_duration_ms": _format_number(sample.audio_duration_ms),
                "rtf": _format_number(sample.rtf),
                "available_execution_providers": _json_cell(
                    sample.available_execution_providers
                ),
                "requested_execution_providers": _json_cell(
                    sample.requested_execution_providers
                ),
                "session_execution_providers": _json_cell(
                    sample.session_execution_providers
                ),
                "provider_selection_status": sample.provider_selection_status,
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
    print(
        f"{title:28s}  {'runs':>4s}  {'fail':>4s}  "
        f"{'total p50':>10s}  {'total p95':>10s}  {'total p99':>10s}  "
        f"{'infer p50':>10s}  {'infer p95':>10s}  {'init p50':>10s}"
    )
    print("------------------------------------------------------------------------")
    for row in rows:
        print(
            f"{row.scenario:28s}  "
            f"{row.successful_runs:4d}  "
            f"{row.failed_runs:4d}  "
            f"{_fmt(row.total_p50_ms):>10s}  "
            f"{_fmt(row.total_p95_ms):>10s}  "
            f"{_fmt(row.total_p99_ms):>10s}  "
            f"{_fmt(row.inference_p50_ms):>10s}  "
            f"{_fmt(row.inference_p95_ms):>10s}  "
            f"{_fmt(row.initialization_p50_ms):>10s}"
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


def _json_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
