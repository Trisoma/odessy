from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TelemetrySnapshot:
    benchmark_process_rss_bytes: int | None
    benchmark_process_swap_bytes: int | None
    process_tree_rss_bytes: int | None
    process_tree_swap_bytes: int | None
    system_ram_used_bytes: int | None
    system_ram_available_bytes: int | None
    system_swap_used_bytes: int | None
    system_swap_available_bytes: int | None
    telemetry_notes: str | None = None


def sample_telemetry_snapshot(
    *,
    root_pid: int | None = None,
    proc_root: Path | None = None,
    meminfo_path: Path | None = None,
) -> TelemetrySnapshot:
    benchmark_process = sample_benchmark_process_memory(root_pid=root_pid, proc_root=proc_root)
    process_tree = sample_process_tree_memory(root_pid=root_pid, proc_root=proc_root)
    system_memory = sample_system_memory(meminfo_path=meminfo_path)
    notes = _merge_notes(
        benchmark_process.telemetry_notes,
        process_tree.telemetry_notes,
        system_memory.telemetry_notes,
    )
    return TelemetrySnapshot(
        benchmark_process_rss_bytes=benchmark_process.rss_bytes,
        benchmark_process_swap_bytes=benchmark_process.swap_bytes,
        process_tree_rss_bytes=process_tree.rss_bytes,
        process_tree_swap_bytes=process_tree.swap_bytes,
        system_ram_used_bytes=system_memory.ram_used_bytes,
        system_ram_available_bytes=system_memory.ram_available_bytes,
        system_swap_used_bytes=system_memory.swap_used_bytes,
        system_swap_available_bytes=system_memory.swap_available_bytes,
        telemetry_notes=notes,
    )


@dataclass(frozen=True)
class MemoryTelemetry:
    rss_bytes: int | None
    swap_bytes: int | None
    telemetry_notes: str | None = None


@dataclass(frozen=True)
class SystemMemoryTelemetry:
    ram_used_bytes: int | None
    ram_available_bytes: int | None
    swap_used_bytes: int | None
    swap_available_bytes: int | None
    telemetry_notes: str | None = None


def sample_benchmark_process_memory(
    *,
    root_pid: int | None = None,
    proc_root: Path | None = None,
) -> MemoryTelemetry:
    pid = root_pid or os.getpid()
    status = _read_status(pid, proc_root=proc_root)
    if status is None:
        return MemoryTelemetry(None, None, "benchmark process status unavailable")
    return MemoryTelemetry(
        rss_bytes=status.rss_bytes,
        swap_bytes=status.swap_bytes,
        telemetry_notes=status.note,
    )


def sample_process_tree_memory(
    *,
    root_pid: int | None = None,
    proc_root: Path | None = None,
) -> MemoryTelemetry:
    pid = root_pid or os.getpid()
    proc_root = proc_root or Path("/proc")
    if not proc_root.exists():
        return MemoryTelemetry(None, None, "process tree telemetry unavailable without procfs")

    records, notes = _scan_procfs(proc_root)
    if pid not in records:
        root_status = _read_status(pid, proc_root=proc_root)
        if root_status is None:
            return MemoryTelemetry(None, None, "process tree root process unavailable")
        records[pid] = root_status

    descendants = _collect_descendants(pid, records)
    total_rss = 0
    total_swap = 0
    seen = 0

    for candidate_pid in [pid, *descendants]:
        status = records.get(candidate_pid)
        if status is None:
            continue
        if status.rss_bytes is not None:
            total_rss += status.rss_bytes
        if status.swap_bytes is not None:
            total_swap += status.swap_bytes
        seen += 1

    note_parts = [part for part in [notes] if part]
    note_parts.append(
        "process-tree telemetry is a point-in-time snapshot; descendants that exit before measurement are omitted"
    )
    return MemoryTelemetry(
        rss_bytes=total_rss if seen else None,
        swap_bytes=total_swap if seen else None,
        telemetry_notes=_merge_notes(*note_parts),
    )


def sample_system_memory(
    *,
    meminfo_path: Path | None = None,
) -> SystemMemoryTelemetry:
    path = meminfo_path or Path("/proc/meminfo")
    if not path.exists():
        return SystemMemoryTelemetry(
            None,
            None,
            None,
            None,
            "system memory telemetry unavailable without procfs",
        )

    fields: dict[str, int] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return SystemMemoryTelemetry(None, None, None, None, f"failed to read meminfo: {exc}")

    for line in text.splitlines():
        name, value = _parse_meminfo_line(line)
        if name is not None and value is not None:
            fields[name] = value

    ram_total = fields.get("MemTotal")
    ram_available = fields.get("MemAvailable")
    if ram_available is None:
        ram_available = _fallback_ram_available(fields)
    swap_total = fields.get("SwapTotal")
    swap_free = fields.get("SwapFree")

    ram_used = None
    if ram_total is not None and ram_available is not None:
        ram_used = max(0, ram_total - ram_available)

    swap_used = None
    if swap_total is not None and swap_free is not None:
        swap_used = max(0, swap_total - swap_free)

    return SystemMemoryTelemetry(
        ram_used_bytes=ram_used,
        ram_available_bytes=ram_available,
        swap_used_bytes=swap_used,
        swap_available_bytes=swap_free,
        telemetry_notes=None,
    )


@dataclass(frozen=True)
class ProcStatus:
    pid: int
    ppid: int
    rss_bytes: int | None
    swap_bytes: int | None
    note: str | None = None


def _read_status(pid: int, *, proc_root: Path | None) -> ProcStatus | None:
    proc_root = proc_root or Path("/proc")
    status_path = proc_root / str(pid) / "status"
    if not status_path.exists():
        return None
    try:
        text = status_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    data: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    parsed_pid = _parse_int_field(data.get("Pid"))
    parsed_ppid = _parse_int_field(data.get("PPid"))
    if parsed_pid is None:
        return None
    rss_bytes = _parse_kb_field(data.get("VmRSS"))
    swap_bytes = _parse_kb_field(data.get("VmSwap"))
    note = None
    if rss_bytes is None and swap_bytes is None:
        note = f"process {parsed_pid} memory unavailable"
    return ProcStatus(
        pid=parsed_pid,
        ppid=parsed_ppid or 0,
        rss_bytes=rss_bytes,
        swap_bytes=swap_bytes,
        note=note,
    )


def _scan_procfs(proc_root: Path) -> tuple[dict[int, ProcStatus], str | None]:
    records: dict[int, ProcStatus] = {}
    notes: list[str] = []
    for pid_dir in proc_root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        status = _read_status(int(pid_dir.name), proc_root=proc_root)
        if status is None:
            continue
        records[status.pid] = status
        if status.note:
            notes.append(status.note)
    return records, _merge_notes(*notes)


def _collect_descendants(root_pid: int, records: dict[int, ProcStatus]) -> list[int]:
    children: dict[int, list[int]] = {}
    for record in records.values():
        children.setdefault(record.ppid, []).append(record.pid)

    descendants: list[int] = []
    pending = list(children.get(root_pid, []))
    seen: set[int] = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        descendants.append(pid)
        pending.extend(children.get(pid, []))

    return descendants


def _parse_meminfo_line(line: str) -> tuple[str | None, int | None]:
    if ":" not in line:
        return None, None
    key, rest = line.split(":", 1)
    parts = rest.strip().split()
    if not parts:
        return key.strip(), None
    try:
        value = int(parts[0])
    except ValueError:
        return key.strip(), None
    unit = parts[1] if len(parts) > 1 else "kB"
    if unit.lower() == "kb":
        value *= 1024
    return key.strip(), value


def _fallback_ram_available(fields: dict[str, int]) -> int | None:
    total = fields.get("MemTotal")
    if total is None:
        return None
    free = fields.get("MemFree", 0)
    buffers = fields.get("Buffers", 0)
    cached = fields.get("Cached", 0)
    reclaimable = fields.get("SReclaimable", 0)
    shmem = fields.get("Shmem", 0)
    return max(0, free + buffers + cached + reclaimable - shmem)


def _parse_int_field(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.split()[0])
    except (ValueError, IndexError):
        return None


def _parse_kb_field(raw: str | None) -> int | None:
    if raw is None:
        return None
    parts = raw.split()
    if not parts:
        return None
    try:
        value = int(parts[0])
    except ValueError:
        return None
    return value * 1024


def _merge_notes(*notes: str | None) -> str | None:
    parts = [note.strip() for note in notes if note and note.strip()]
    if not parts:
        return None
    return "; ".join(dict.fromkeys(parts))
