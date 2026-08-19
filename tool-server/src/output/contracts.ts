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
  exit_code: number;
  duration: number;
  status: "ok" | "failed" | "timeout";
  command: string;
  cwd: string;
  timed_out: boolean;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
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
