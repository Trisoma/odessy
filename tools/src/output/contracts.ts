import type { SandboxReport } from "../execution/seam.js";
import type { SpillLocator } from "./spill.js";

export type ToolStatus = "ok" | "not_found" | "failed";

export interface Truncation {
  truncated: boolean;
  maxBytes: number;
  originalBytes: number;
}

export interface FilesystemReadResult {
  path: string;
  content: string;
  size: number;
  status: ToolStatus;
  truncated: boolean;
  truncation?: Truncation;
}

export interface FilesystemListEntry {
  name: string;
  path: string;
  type: "file" | "directory" | "other";
  size: number;
}

export interface FilesystemListResult {
  path: string;
  entries: FilesystemListEntry[];
  status: ToolStatus;
  truncated: boolean;
}

export interface ProjectFindResult {
  pattern: string;
  path: string;
  matches: string[];
  count: number;
  status: ToolStatus;
  truncated: boolean;
}

export interface ProjectGrepMatch {
  path: string;
  line: number;
  text: string;
}

export interface ProjectGrepResult {
  pattern: string;
  path: string;
  matches: ProjectGrepMatch[];
  count: number;
  status: ToolStatus;
  truncated: boolean;
}

export interface GitTextResult {
  status: ToolStatus;
  raw?: string;
  diff?: string;
  error?: string;
}

export interface GitChangedFilesResult {
  status: ToolStatus;
  files: string[];
  error?: string;
}

export interface GitLogResult {
  status: ToolStatus;
  commits: string[];
  limit: number;
  error?: string;
}

export interface TerminalRunResult {
  stdout: string;
  stderr: string;
  /**
   * The process's own exit status, or `null` when there is none to report.
   *
   * `timed_out` distinguishes the cases. When the executor terminated the process,
   * the code a killed process happens to report (1 on Windows) is an artifact of the
   * kill rather than an outcome of the command, so reporting it would be misleading.
   * `null` says exactly "no process exit code exists"; a sentinel such as -1 is
   * indistinguishable from a real code and so reads as the command's own result.
   *
   * A spawn failure does not produce a result at all: it rejects.
   */
  exit_code: number | null;
  duration: number;
  status: "ok" | "failed" | "timeout" | "refused";
  command: string;
  cwd: string;
  timed_out: boolean;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
  /**
   * Locator for the withheld remainder of each stream.
   *
   * Absent when nothing was withheld, and also absent when spilling *failed* — a
   * storage failure returns the untruncated text instead of a lossy cut plus a
   * locator pointing at a file that does not exist. See `output/prepare.ts`.
   */
  stdout_spill?: SpillLocator | null;
  stderr_spill?: SpillLocator | null;
  /**
   * What confinement achieved for this run. Always present, because a caller
   * cannot tell a confined run from an unconfined one without it — and the two
   * demand different amounts of trust.
   */
  sandbox: SandboxReport;
}

export interface FilesystemWriteResult {
  path: string;
  bytes_written: number;
  status: ToolStatus;
}

export interface LogAppendResult {
  path: string;
  bytes_written: number;
  status: ToolStatus;
}
