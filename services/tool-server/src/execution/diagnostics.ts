/**
 * Runner-failure versus command-denial classification.
 *
 * Moved from `dsh-sandbox/src/diagnostics.ts`, whole. This is the module that
 * keeps two very different outcomes apart:
 *
 * ```text
 * runner failure   the command never ran, because the confinement runner failed
 * denial           confinement worked and blocked the command
 * ```
 *
 * Conflating them turns a broken sandbox into a confusing command error, and a
 * command error into apparent evidence that confinement worked.
 *
 * The Python side reimplements this rather than porting it, because the error
 * shapes are language-specific: Node classifies `EACCES`/`ENOENT` spawn
 * rejections carrying a `syscall` string, Python classifies
 * `FileNotFoundError`/`PermissionError`.
 */

import { accessSync, constants, statSync } from "node:fs";

import type { RunnerFailureRule } from "./contract.js";

/** Spawn codes proven to identify executable resolution or permission failure. */
const EXECUTABLE_SPAWN_CODES = new Set(["EACCES", "ENOENT"]);

/** Whether the caller-owned spawn cwd can be entered. */
function isUsableWorkdir(path: string): boolean {
  try {
    if (!statSync(path).isDirectory()) {
      return false;
    }
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Whether a spawn rejection is evidence that the *runner* failed to start.
 *
 * Only Node `ENOENT`/`EACCES` failures whose error path equals `argv[0]` are
 * attributed, after independently ruling out the caller-owned cwd. A supplied
 * error path must exactly identify the runner; without one, the syscall must.
 *
 * The working directory is checked at classification time, not atomically with
 * the spawn. Concurrent path replacement may therefore change attribution, but
 * it cannot permit an unconfined execution.
 */
export function isRunnerSpawnFailure(
  error: unknown,
  runnerProgram: string | undefined,
  workdir: string,
): boolean {
  if (runnerProgram === undefined || !isUsableWorkdir(workdir)) {
    return false;
  }
  if (typeof error !== "object" || error === null) {
    return false;
  }
  const { code, path, syscall } = error as { code?: unknown; path?: unknown; syscall?: unknown };
  if (typeof code !== "string" || !EXECUTABLE_SPAWN_CODES.has(code)) {
    return false;
  }
  if (typeof syscall !== "string") {
    return false;
  }
  const exactSyscall = `spawn ${runnerProgram}`;
  if (path === undefined) {
    return syscall === exactSyscall;
  }
  if (typeof path !== "string" || path.length === 0 || path !== runnerProgram) {
    return false;
  }
  return syscall === "spawn" || syscall === exactSyscall;
}

/** Fatal runner evidence, retained for the caller's error detail. */
export interface RunnerFailureMatch {
  /** The original stderr line that matched a fatal signature. */
  detail: string;
}

/**
 * Classify one settled process against the selected backend's structured rules.
 *
 * Each rule requires a non-zero exit, its optional exit-code gate, and a fatal
 * signature on one stderr line after exact informational lines are excluded.
 * Exit status alone never proves runner failure.
 */
export function classifyRunnerFailure(
  exitCode: number | null,
  stderr: string,
  rules: readonly RunnerFailureRule[],
): RunnerFailureMatch | undefined {
  if (exitCode === null || exitCode === 0) {
    return undefined;
  }
  const lines = stderr.split(/\r?\n/);
  for (const rule of rules) {
    if (rule.allowedExitCodes !== undefined && !rule.allowedExitCodes.includes(exitCode)) {
      continue;
    }
    const informational = new Set(
      (rule.informationalLines ?? []).map((line) => line.toLowerCase()),
    );
    // An empty or whitespace-only substring is not meaningful evidence. Drop it
    // while keeping any valid signatures beside it active.
    const fatal = rule.fatalSignatures
      .filter((signature) => signature.trim().length > 0)
      .map((signature) => signature.toLowerCase());
    for (const line of lines) {
      const lowered = line.toLowerCase();
      if (informational.has(lowered)) {
        continue;
      }
      if (fatal.some((signature) => lowered.includes(signature))) {
        return { detail: line };
      }
    }
  }
  return undefined;
}

/**
 * Whether a non-zero exit's stderr matches one of the backend's deny signatures.
 *
 * `signatures` is the *selected backend's* dialect, never a cross-backend union:
 * a union claims denials a given backend never produces.
 */
export function matchesSignature(
  exitCode: number | null,
  stderr: string,
  signatures: readonly string[],
): boolean {
  if (exitCode === null || exitCode === 0) {
    return false;
  }
  const lowered = stderr.toLowerCase();
  return signatures.some((signature) => lowered.includes(signature.toLowerCase()));
}
