# This is the result template and result generator
from __future__ import annotations

import os
import platform
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .telemetry import (
    TelemetrySnapshot,
    sample_benchmark_process_memory,
    sample_process_tree_memory,
    sample_system_memory,
    sample_telemetry_snapshot,
)

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sample_process_memory() -> dict[str, int | None]:
    """Backward-compatible benchmark-process-only memory helper."""
    sample = sample_benchmark_process_memory()
    return {
        "benchmark_process_rss_bytes": sample.rss_bytes,
        "benchmark_process_swap_bytes": sample.swap_bytes,
    }

def _git_commit(workspace_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace_root), "rev-parse", "--short", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None

@dataclass
class BenchmarkMetadata:
    created_at: str
    workspace_root: str
    output_dir: str
    host: str
    platform: str
    python_version: str
    odessy_version: str
    git_commit: str | None
    cpu_count: int | None
    extra: dict[str, Any] = field(default_factory=dict)

@dataclass
class BenchmarkOutcome:
    status: str
    stage_ms: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    error: str | None = None

@dataclass
class BenchmarkSample:
    scenario: str
    iteration: int
    is_warmup: bool
    status: str
    total_ms: float
    stage_ms: dict[str, float] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    benchmark_process_rss_before_bytes: int | None = None
    benchmark_process_rss_after_bytes: int | None = None
    benchmark_process_swap_before_bytes: int | None = None
    benchmark_process_swap_after_bytes: int | None = None
    process_tree_rss_before_bytes: int | None = None
    process_tree_rss_after_bytes: int | None = None
    process_tree_swap_before_bytes: int | None = None
    process_tree_swap_after_bytes: int | None = None
    system_ram_used_before_bytes: int | None = None
    system_ram_used_after_bytes: int | None = None
    system_ram_available_before_bytes: int | None = None
    system_ram_available_after_bytes: int | None = None
    system_swap_used_before_bytes: int | None = None
    system_swap_used_after_bytes: int | None = None
    system_swap_available_before_bytes: int | None = None
    system_swap_available_after_bytes: int | None = None
    telemetry_notes: str | None = None
    notes: str | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    agent_turn_total_ms: float | None = None
    proposal_generation_ms: float | None = None
    proposal_parsing_ms: float | None = None
    repair_generation_ms: float | None = None
    policy_validation_ms: float | None = None
    mcp_execution_ms: float | None = None
    final_generation_ms: float | None = None
    model_total_duration_ms: float | None = None
    prompt_eval_duration_ms: float | None = None
    prompt_eval_count: int | None = None
    eval_duration_ms: float | None = None
    eval_count: int | None = None
    generation_tokens_per_sec: float | None = None
    model_call_count: int | None = None
    thinking_exposed: bool | None = None
    thinking_chars: int | None = None
    thinking_token_count: int | None = None
    response_chars: int | None = None
    configuration: str | None = None
    model_name: str | None = None
    think_setting: bool | str | None = None
    expected_tool: str | None = None
    selected_tool: str | None = None
    tool_selection_correct: bool | None = None
    proposal_valid: bool | None = None
    repair_triggered: bool | None = None
    tool_called: bool | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    profile: str | None = None
    component: str | None = None
    execution_path: str | None = None
    lifecycle: str | None = None
    success: bool = False
    hardware: str | None = None
    runtime_version: str | None = None
    pytorch_device: str | None = None
    model_precision: str | None = None
    batch_size: int | None = None
    warmup_iterations: int | None = None
    measurement_iterations: int | None = None
    initialization_ms: float | None = None
    inference_ms: float | None = None
    audio_duration_ms: float | None = None
    rtf: float | None = None
    available_execution_providers: list[str] | None = None
    requested_execution_providers: list[Any] | None = None
    session_execution_providers: list[str] | None = None
    provider_selection_status: str | None = None

@dataclass
class BenchmarkSummary:
    scenario: str
    warmup_runs: int
    attempted_runs: int
    successful_runs: int
    failed_runs: int
    min_ms: float | None
    mean_ms: float | None
    median_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    inference_p50_ms: float | None = None
    inference_p95_ms: float | None = None
    inference_p99_ms: float | None = None
    total_p50_ms: float | None = None
    total_p95_ms: float | None = None
    total_p99_ms: float | None = None
    initialization_p50_ms: float | None = None
    initialization_p95_ms: float | None = None
    initialization_p99_ms: float | None = None

@dataclass
class BenchmarkRun:
    metadata: BenchmarkMetadata
    samples: list[BenchmarkSample]
    summaries: list[BenchmarkSummary]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": asdict(self.metadata),
            "samples": [asdict(sample) for sample in self.samples],
            "summaries": [asdict(summary) for summary in self.summaries],
        }

class BenchmarkScenario(Protocol):
    name: str

    def run_once(self, *, iteration: int, is_warmup: bool) -> BenchmarkOutcome:
        ...

class BenchmarkRunner:
    def __init__(
        self,
        *,
        runs: int,
        warmup_runs: int,
        workspace_root: Path,
        output_dir: Path,
        capture_memory: bool = True,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.runs = max(0, runs)
        self.warmup_runs = max(0, warmup_runs)
        self.workspace_root = workspace_root
        self.output_dir = output_dir
        self.capture_memory = capture_memory
        self.extra_metadata = extra_metadata or {}

    def run(self, scenarios: list[BenchmarkScenario]) -> BenchmarkRun:
        metadata = BenchmarkMetadata(
            created_at=utc_now_iso(),
            workspace_root=str(self.workspace_root),
            output_dir=str(self.output_dir),
            host=socket.gethostname(),
            platform=platform.platform(),
            python_version=sys.version.split()[0],
            odessy_version="0.1.0",
            git_commit=_git_commit(self.workspace_root),
            cpu_count=os.cpu_count(),
            extra=dict(self.extra_metadata),
        )

        samples: list[BenchmarkSample] = []
        for scenario in scenarios:
            prepare = getattr(scenario, "prepare", None)
            if callable(prepare):
                prepare()
            for iteration in range(self.warmup_runs):
                samples.append(self._execute(scenario, iteration, True))
            for iteration in range(self.runs):
                samples.append(self._execute(scenario, iteration, False))
            close = getattr(scenario, "close", None)
            if callable(close):
                close()

        summaries = _summaries(samples, self.warmup_runs)
        return BenchmarkRun(metadata=metadata, samples=samples, summaries=summaries)

    def _execute(
        self,
        scenario: BenchmarkScenario,
        iteration: int,
        is_warmup: bool,
    ) -> BenchmarkSample:
        started_at = utc_now_iso()
        before = sample_telemetry_snapshot() if self.capture_memory else _empty_telemetry()
        start = time.perf_counter()
        try:
            outcome = scenario.run_once(iteration=iteration, is_warmup=is_warmup)
            status = outcome.status
            stage_ms = dict(outcome.stage_ms)
            notes = outcome.notes
            error = outcome.error
            details = dict(outcome.details)
        except Exception as exc:
            status = "failed"
            stage_ms = {}
            notes = None
            error = str(exc)
            details = {}
        measured_total_ms = details.get("total_ms")
        if isinstance(measured_total_ms, (int, float)):
            total_ms = float(measured_total_ms)
        else:
            total_ms = (time.perf_counter() - start) * 1000.0
        after = sample_telemetry_snapshot() if self.capture_memory else _empty_telemetry()
        return BenchmarkSample(
            scenario=scenario.name,
            iteration=iteration,
            is_warmup=is_warmup,
            status=status,
            total_ms=total_ms,
            stage_ms=stage_ms,
            started_at=started_at,
            finished_at=utc_now_iso(),
            benchmark_process_rss_before_bytes=before.benchmark_process_rss_bytes,
            benchmark_process_rss_after_bytes=after.benchmark_process_rss_bytes,
            benchmark_process_swap_before_bytes=before.benchmark_process_swap_bytes,
            benchmark_process_swap_after_bytes=after.benchmark_process_swap_bytes,
            process_tree_rss_before_bytes=before.process_tree_rss_bytes,
            process_tree_rss_after_bytes=after.process_tree_rss_bytes,
            process_tree_swap_before_bytes=before.process_tree_swap_bytes,
            process_tree_swap_after_bytes=after.process_tree_swap_bytes,
            system_ram_used_before_bytes=before.system_ram_used_bytes,
            system_ram_used_after_bytes=after.system_ram_used_bytes,
            system_ram_available_before_bytes=before.system_ram_available_bytes,
            system_ram_available_after_bytes=after.system_ram_available_bytes,
            system_swap_used_before_bytes=before.system_swap_used_bytes,
            system_swap_used_after_bytes=after.system_swap_used_bytes,
            system_swap_available_before_bytes=before.system_swap_available_bytes,
            system_swap_available_after_bytes=after.system_swap_available_bytes,
            telemetry_notes=_merge_notes(before.telemetry_notes, after.telemetry_notes),
            notes=notes,
            error=error,
            details=details,
            agent_turn_total_ms=_detail_value(details, "agent_turn_total_ms"),
            proposal_generation_ms=_detail_value(details, "proposal_generation_ms"),
            proposal_parsing_ms=_detail_value(details, "proposal_parsing_ms"),
            repair_generation_ms=_detail_value(details, "repair_generation_ms"),
            policy_validation_ms=_detail_value(details, "policy_validation_ms"),
            mcp_execution_ms=_detail_value(details, "mcp_execution_ms"),
            final_generation_ms=_detail_value(details, "final_generation_ms"),
            model_total_duration_ms=_detail_value(details, "model_total_duration_ms"),
            prompt_eval_duration_ms=_detail_value(details, "prompt_eval_duration_ms"),
            prompt_eval_count=_detail_value(details, "prompt_eval_count"),
            eval_duration_ms=_detail_value(details, "eval_duration_ms"),
            eval_count=_detail_value(details, "eval_count"),
            generation_tokens_per_sec=_detail_value(
                details, "generation_tokens_per_sec"
            ),
            model_call_count=_detail_value(details, "model_call_count"),
            thinking_exposed=_detail_value(details, "thinking_exposed"),
            thinking_chars=_detail_value(details, "thinking_chars"),
            thinking_token_count=_detail_value(details, "thinking_token_count"),
            response_chars=_detail_value(details, "response_chars"),
            configuration=_detail_value(details, "configuration"),
            model_name=_detail_value(details, "model"),
            think_setting=_detail_value(details, "think_setting"),
            expected_tool=_detail_value(details, "expected_tool"),
            selected_tool=_detail_value(details, "selected_tool"),
            tool_selection_correct=_detail_value(
                details, "tool_selection_correct"
            ),
            proposal_valid=_detail_value(details, "proposal_valid"),
            repair_triggered=_detail_value(details, "repair_triggered"),
            tool_called=_detail_value(details, "tool_called"),
            prompt_tokens=_detail_value(details, "prompt_tokens"),
            completion_tokens=_detail_value(details, "completion_tokens"),
            profile=_detail_value(details, "profile"),
            component=_detail_value(details, "component"),
            execution_path=_detail_value(details, "execution_path"),
            lifecycle=_detail_value(details, "lifecycle"),
            success=status == "ok",
            hardware=_detail_value(details, "hardware"),
            runtime_version=_detail_value(details, "runtime_version"),
            pytorch_device=_detail_value(details, "pytorch_device"),
            model_precision=_detail_value(details, "model_precision"),
            batch_size=_detail_value(details, "batch_size"),
            warmup_iterations=_detail_value(details, "warmup_iterations"),
            measurement_iterations=_detail_value(details, "measurement_iterations"),
            initialization_ms=_detail_value(details, "initialization_ms"),
            inference_ms=_detail_value(details, "inference_ms"),
            audio_duration_ms=_detail_value(details, "audio_duration_ms"),
            rtf=_detail_value(details, "rtf"),
            available_execution_providers=_detail_value(
                details, "available_execution_providers"
            ),
            requested_execution_providers=_detail_value(
                details, "requested_execution_providers"
            ),
            session_execution_providers=_detail_value(
                details, "session_execution_providers"
            ),
            provider_selection_status=_detail_value(
                details, "provider_selection_status"
            ),
        )

def _summaries(samples: list[BenchmarkSample], warmup_runs: int) -> list[BenchmarkSummary]:
    by_scenario: dict[str, list[BenchmarkSample]] = {}
    for sample in samples:
        if sample.is_warmup:
            continue
        by_scenario.setdefault(sample.scenario, []).append(sample)

    summaries: list[BenchmarkSummary] = []
    for scenario, scenario_samples in by_scenario.items():
        successful = [sample.total_ms for sample in scenario_samples if sample.status == "ok"]
        failed = len(scenario_samples) - len(successful)
        successful_samples = [sample for sample in scenario_samples if sample.status == "ok"]
        inference_values = [
            sample.inference_ms
            for sample in successful_samples
            if sample.inference_ms is not None
        ]
        initialization_values = [
            sample.initialization_ms
            for sample in successful_samples
            if sample.initialization_ms is not None
        ]
        summaries.append(
            BenchmarkSummary(
                scenario=scenario,
                warmup_runs=warmup_runs,
                attempted_runs=len(scenario_samples),
                successful_runs=len(successful),
                failed_runs=failed,
                min_ms=_stat_min(successful),
                mean_ms=_stat_mean(successful),
                median_ms=_stat_median(successful),
                p95_ms=_stat_percentile(successful, 95.0),
                max_ms=_stat_max(successful),
                inference_p50_ms=_stat_percentile(inference_values, 50.0),
                inference_p95_ms=_stat_percentile(inference_values, 95.0),
                inference_p99_ms=_stat_percentile(inference_values, 99.0),
                total_p50_ms=_stat_percentile(successful, 50.0),
                total_p95_ms=_stat_percentile(successful, 95.0),
                total_p99_ms=_stat_percentile(successful, 99.0),
                initialization_p50_ms=_stat_percentile(initialization_values, 50.0),
                initialization_p95_ms=_stat_percentile(initialization_values, 95.0),
                initialization_p99_ms=_stat_percentile(initialization_values, 99.0),
            )
        )
    return sorted(summaries, key=lambda item: item.scenario)

def _stat_min(values: list[float]) -> float | None:
    return min(values) if values else None

def _stat_max(values: list[float]) -> float | None:
    return max(values) if values else None

def _stat_mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None

def _stat_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None

def _stat_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

def _empty_telemetry() -> TelemetrySnapshot:
    return TelemetrySnapshot(
        benchmark_process_rss_bytes=None,
        benchmark_process_swap_bytes=None,
        process_tree_rss_bytes=None,
        process_tree_swap_bytes=None,
        system_ram_used_bytes=None,
        system_ram_available_bytes=None,
        system_swap_used_bytes=None,
        system_swap_available_bytes=None,
        telemetry_notes=None,
    )

def _merge_notes(*notes: str | None) -> str | None:
    parts = [note.strip() for note in notes if note and note.strip()]
    if not parts:
        return None
    return "; ".join(dict.fromkeys(parts))

def _detail_value(details: dict[str, Any], key: str) -> Any:
    return details.get(key)
