/**
 * Resource limits for one confined command, sent by the authority layer with the call.
 *
 * The Python executor applies them with `setrlimit` (POSIX) or a job object (Windows).
 * This side applies them on POSIX through util-linux `prlimit`, wrapped outermost so
 * the limits are inherited through bwrap and by every process the command starts.
 * Without `prlimit` (macOS, a minimal image) or on Windows they are reported as not
 * applied rather than assumed. The process count is never applied on POSIX:
 * `RLIMIT_NPROC` counts every process of the user, not of the command.
 */

import { spawnSync } from "node:child_process";

export interface ResourceLimits {
  readonly memory_mb?: number;
  readonly processes?: number;
  readonly file_size_mb?: number;
  readonly cpu_seconds?: number;
}

export interface LimitReport {
  readonly applied: Readonly<Record<string, number>>;
  readonly not_applied: readonly string[];
}

/** Ceilings a call may ask for; larger values are clamped. Same as Python. */
const CEILINGS: Readonly<Record<keyof ResourceLimits, number>> = {
  memory_mb: 1 << 20,
  processes: 4096,
  file_size_mb: 1 << 20,
  cpu_seconds: 3600,
};

let prlimitPath: string | null | undefined;

function prlimit(): string | null {
  if (prlimitPath === undefined) {
    if (process.platform === "win32") {
      prlimitPath = null;
    } else {
      const probe = spawnSync("prlimit", ["--version"], { stdio: "ignore" });
      prlimitPath = probe.status === 0 ? "prlimit" : null;
    }
  }
  return prlimitPath;
}

/** Limits from a call, validated and clamped; undefined when it sent none. */
export function normalizeLimits(value: ResourceLimits | undefined): ResourceLimits | undefined {
  if (value === undefined) return undefined;
  const result: Record<string, number> = {};
  for (const [key, ceiling] of Object.entries(CEILINGS)) {
    const raw = (value as Record<string, unknown>)[key];
    if (typeof raw === "number" && Number.isInteger(raw) && raw > 0) {
      result[key] = Math.min(raw, ceiling);
    }
  }
  return Object.keys(result).length > 0 ? result : undefined;
}

/** `argv` under `prlimit`, and what was applied. */
export function limitArgv(
  limits: ResourceLimits | undefined,
  argv: readonly string[],
): { argv: readonly string[]; report: LimitReport } {
  if (limits === undefined) {
    return { argv, report: { applied: {}, not_applied: [] } };
  }
  const requested = Object.entries(limits) as [keyof ResourceLimits, number][];
  const runner = prlimit();
  if (runner === null) {
    return { argv, report: { applied: {}, not_applied: requested.map(([key]) => key).sort() } };
  }
  const flags: string[] = [];
  const applied: Record<string, number> = {};
  for (const [key, value] of requested) {
    if (key === "memory_mb") flags.push(`--data=${value * 1024 * 1024}`);
    else if (key === "file_size_mb") flags.push(`--fsize=${value * 1024 * 1024}`);
    else if (key === "cpu_seconds") flags.push(`--cpu=${value}`);
    else continue;
    applied[key] = value;
  }
  const notApplied = requested.map(([key]) => key).filter((key) => !(key in applied)).sort();
  if (flags.length === 0) {
    return { argv, report: { applied, not_applied: notApplied } };
  }
  return { argv: [runner, ...flags, "--", ...argv], report: { applied, not_applied: notApplied } };
}

/** The limit a POSIX exit points to: a shell reports a killed child as 128 + signal. */
export function exceededLimit(code: number | null, signal: NodeJS.Signals | null): string | undefined {
  if (process.platform === "win32") return undefined;
  if (signal === "SIGXCPU" || code === 128 + 24) return "cpu_seconds";
  if (signal === "SIGXFSZ" || code === 128 + 25) return "file_size_mb";
  return undefined;
}
