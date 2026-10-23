import { execFile, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { z } from "zod";

import type {
  FilesystemListResult,
  FilesystemReadResult,
  FilesystemWriteResult,
  GitChangedFilesResult,
  GitLogResult,
  GitTextResult,
  LogAppendResult,
  ProjectFindResult,
  ProjectGrepMatch,
  ProjectGrepResult,
  TerminalRunResult,
} from "../output/contracts.js";
import { DEFAULT_MAX_BYTES, normalizeLimit, truncateUtf8 } from "../output/truncate.js";
import { prepareOutput, redactResult } from "../output/prepare.js";
import { redactText } from "../output/redaction.js";
import { SpillStore } from "../output/spill.js";
import { confineArgv, type Confinement, type SandboxReport } from "../execution/seam.js";
import { ISOLATION_REQUIREMENTS, MODES } from "../execution/contract.js";
import type { WorkspaceGuard } from "../workspace/path-guard.js";

const execFileAsync = promisify(execFile);
const SKIP_DIRS = new Set([".git", "node_modules", "__pycache__", ".pytest_cache", "dist"]);

/** Inline budget for one git text field; the remainder is spilled. */
const GIT_TEXT_MAX_CHARS = 64 * 1024;

export const readInputSchema = {
  path: z.string(),
  max_bytes: z.number().int().min(1).max(128 * 1024).optional(),
};

export const listInputSchema = {
  path: z.string().optional(),
  limit: z.number().int().min(1).max(100).optional(),
};

export const findInputSchema = {
  pattern: z.string(),
  path: z.string().optional(),
  limit: z.number().int().min(1).max(100).optional(),
};

export const grepInputSchema = {
  pattern: z.string(),
  path: z.string().optional(),
  glob: z.string().optional(),
  ignore_case: z.boolean().optional(),
  literal: z.boolean().optional(),
  context: z.number().int().min(0).max(5).optional(),
  limit: z.number().int().min(1).max(100).optional(),
};

export const gitDiffInputSchema = {
  mode: z.enum(["working", "cached"]).optional(),
};

export const gitChangedFilesInputSchema = {
  mode: z.enum(["working", "cached", "all"]).optional(),
};

export const gitLogInputSchema = {
  limit: z.number().int().min(1).max(500).optional(),
};

export const laptopTerminalRunInputSchema = {
  command: z.string(),
  cwd: z.string().optional(),
  timeout: z.number().int().min(1).max(30).optional(),
  /**
   * The sandbox mode the *authority layer* resolved for this call.
   *
   * Sent by the Odessy agent after policy and any escalation, never by a model. The
   * host does not decide it; it selects a provider that satisfies it. Omitting it
   * means the caller resolved no mode, and the run is reported as unconfined rather
   * than assumed to be confined.
   *
   * Declared with the closed vocabulary so an unknown mode is rejected here instead of
   * being silently dropped. That silent drop was a real defect: the agent logged a
   * resolved mode, the host discarded it, and the record claimed a boundary that did
   * not exist.
   */
  mode: z.enum(MODES).optional(),
  /** How strict the peer is about isolation. `required` cannot be satisfied by a
   * mode that provides no confinement. */
  isolation: z.enum(ISOLATION_REQUIREMENTS).optional(),
};

export const filesystemWriteInputSchema = {
  path: z.string(),
  content: z.string(),
};

export const logAppendInputSchema = {
  content: z.string(),
};

export async function readFileTool(
  guard: WorkspaceGuard,
  input: { path: string; max_bytes?: number },
): Promise<FilesystemReadResult> {
  const absolutePath = await guard.resolveExisting(input.path);
  const stat = await fs.promises.stat(absolutePath);
  if (!stat.isFile()) {
    throw new Error(`filesystem.read requires a regular file: ${input.path}`);
  }
  const raw = await fs.promises.readFile(absolutePath, "utf8");
  const maxBytes = input.max_bytes ?? DEFAULT_MAX_BYTES;
  // Redact -> retain -> spill, the same pipeline terminal output uses. A secret in
  // a file must never reach the spill file, and a cut file must stay recoverable
  // instead of being silently shortened at the head.
  const prepared = prepareOutput(raw, {
    label: "file content",
    maxChars: maxBytes,
    spill: new SpillStore(guard.root),
  });
  return {
    path: guard.relative(absolutePath),
    content: prepared.text,
    size: stat.size,
    status: "ok",
    truncated: prepared.truncated,
    ...(prepared.truncated
      ? {
          truncation: {
            truncated: true,
            maxBytes,
            originalBytes: Buffer.byteLength(raw, "utf8"),
          },
        }
      : {}),
    ...(prepared.spill ? { spill: prepared.spill } : {}),
  };
}

export async function listDirectoryTool(
  guard: WorkspaceGuard,
  input: { path?: string; limit?: number },
): Promise<FilesystemListResult> {
  const absolutePath = await guard.resolveExisting(input.path ?? ".");
  const stat = await fs.promises.stat(absolutePath);
  if (!stat.isDirectory()) {
    throw new Error(`filesystem.list requires a directory: ${input.path ?? "."}`);
  }
  const limit = normalizeLimit(input.limit);
  const names = (await fs.promises.readdir(absolutePath)).sort((a, b) => a.localeCompare(b));
  const visible = names.slice(0, limit);
  const entries = await Promise.all(
    visible.map(async (name) => {
      const entryPath = path.join(absolutePath, name);
      const entryStat = await fs.promises.stat(entryPath);
      return {
        name,
        path: guard.relative(entryPath),
        type: entryStat.isDirectory() ? "directory" as const : entryStat.isFile() ? "file" as const : "other" as const,
        size: entryStat.size,
      };
    }),
  );
  return redactResult({
    path: guard.relative(absolutePath),
    entries,
    status: "ok" as const,
    truncated: names.length > visible.length,
  });
}

export async function findProjectTool(
  guard: WorkspaceGuard,
  input: { pattern: string; path?: string; limit?: number },
): Promise<ProjectFindResult> {
  const root = await guard.resolveExisting(input.path ?? ".");
  const limit = normalizeLimit(input.limit);
  const matches: string[] = [];

  await walk(root, async (absolutePath, stat) => {
    if (matches.length >= limit) return;
    if (stat.isFile() && fileNameMatches(path.basename(absolutePath), input.pattern)) {
      matches.push(guard.relative(absolutePath));
    }
  });

  return redactResult({
    pattern: input.pattern,
    path: guard.relative(root),
    matches,
    count: matches.length,
    status: "ok" as const,
    truncated: matches.length >= limit,
  });
}

export async function grepProjectTool(
  guard: WorkspaceGuard,
  input: {
    pattern: string;
    path?: string;
    glob?: string;
    ignore_case?: boolean;
    literal?: boolean;
    context?: number;
    limit?: number;
  },
): Promise<ProjectGrepResult> {
  const root = await guard.resolveExisting(input.path ?? ".");
  const limit = normalizeLimit(input.limit);
  const matches: ProjectGrepMatch[] = [];
  const matcher = buildMatcher(input.pattern, {
    ignoreCase: input.ignore_case === true,
    literal: input.literal === true,
  });

  await walk(root, async (absolutePath, stat) => {
    if (matches.length >= limit || !stat.isFile()) return;
    if (input.glob && !globMatches(path.basename(absolutePath), input.glob)) return;
    const text = await fs.promises.readFile(absolutePath, "utf8").catch(() => "");
    const lines = text.split(/\r?\n/);
    for (let index = 0; index < lines.length && matches.length < limit; index += 1) {
      if (matcher(lines[index] ?? "")) {
        matches.push({
          path: guard.relative(absolutePath),
          line: index + 1,
          // Redact BEFORE bounding. A token cut in half no longer matches any
          // pattern, so truncating first would let its prefix through.
          text: truncateUtf8(redactText(lines[index] ?? ""), 500).content,
        });
      }
    }
  });

  return redactResult({
    pattern: input.pattern,
    path: guard.relative(root),
    matches,
    count: matches.length,
    status: "ok" as const,
    truncated: matches.length >= limit,
  });
}

export async function gitStatus(guard: WorkspaceGuard): Promise<GitTextResult> {
  return prepareGitText(
    await gitText(guard.root, ["status", "--porcelain=v1", "--branch"], "raw"),
    "git.status",
    guard.root,
  );
}

export async function gitDiff(
  guard: WorkspaceGuard,
  input: { mode?: "working" | "cached" },
): Promise<GitTextResult> {
  const args =
    input.mode === "cached"
      ? ["diff", "--cached", "--no-ext-diff"]
      : ["diff", "--no-ext-diff"];
  return prepareGitText(await gitText(guard.root, args, "diff"), "git.diff", guard.root);
}

export async function gitChangedFiles(
  guard: WorkspaceGuard,
  input: { mode?: "working" | "cached" | "all" },
): Promise<GitChangedFilesResult> {
  const modes = input.mode === "all" ? ["working", "cached"] : [input.mode ?? "working"];
  const files = new Set<string>();
  for (const mode of modes) {
    const args = mode === "cached" ? ["diff", "--cached", "--name-only"] : ["diff", "--name-only"];
    const result = await gitText(guard.root, args, "raw");
    if (result.status !== "ok") {
      return redactResult({ status: "failed" as const, files: [], error: result.error });
    }
    for (const line of (result.raw ?? "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean)) {
      files.add(line.replaceAll("\\", "/"));
    }
  }
  return redactResult({ status: "ok" as const, files: [...files].sort() });
}

export async function gitLog(
  guard: WorkspaceGuard,
  input: { limit?: number },
): Promise<GitLogResult> {
  const limit = Math.max(1, Math.min(Math.floor(input.limit ?? 20), 50));
  const result = await gitText(guard.root, ["log", "--oneline", "-n", String(limit)], "raw");
  if (result.status !== "ok") {
    return redactResult({ status: "failed" as const, commits: [], limit, error: result.error });
  }
  return redactResult({
    status: "ok" as const,
    commits: (result.raw ?? "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
    limit,
  });
}

export async function laptopTerminalRun(
  guard: WorkspaceGuard,
  input: { command: string; cwd?: string; timeout?: number },
  confinement?: Confinement,
): Promise<TerminalRunResult> {
  const resolvedCwd = await guard.resolveExisting(input.cwd ?? ".");
  const effectiveTimeout = Math.min(Math.max(1, input.timeout ?? 10), 30);
  const start = Date.now();
  const result = await runShellCommand(
    input.command,
    resolvedCwd,
    effectiveTimeout,
    confinement,
    guard.root,
  );
  const duration = (Date.now() - start) / 1000;
  // One store per call, shared by both streams, so a run's captured output lands
  // in a single session directory. Redact -> retain -> spill, in that order: a
  // secret must never reach the spill file, because once it is on disk it cannot
  // be recalled.
  const spill = new SpillStore(guard.root);
  const stdout = prepareOutput(result.stdout, {
    label: "stdout",
    maxChars: 64 * 1024,
    spill,
  });
  const stderr = prepareOutput(result.stderr, {
    label: "stderr",
    maxChars: 32 * 1024,
    spill,
  });
  return redactResult({
    stdout: stdout.text,
    stderr: stderr.text,
    exit_code: result.exitCode,
    duration,
    status: result.timedOut ? ("timeout" as const) : result.exitCode === 0 ? ("ok" as const) : ("failed" as const),
    command: input.command,
    cwd: resolvedCwd,
    timed_out: result.timedOut,
    stdout_truncated: stdout.truncated,
    stderr_truncated: stderr.truncated,
    stdout_spill: stdout.spill,
    stderr_spill: stderr.spill,
    sandbox: result.sandbox,
  });
}

export async function filesystemWriteTool(
  guard: WorkspaceGuard,
  input: { path: string; content: string },
): Promise<FilesystemWriteResult> {
  const absolutePath = guard.resolveCandidate(input.path);
  await fs.promises.mkdir(path.dirname(absolutePath), { recursive: true });
  await fs.promises.writeFile(absolutePath, input.content, "utf8");
  return redactResult({
    path: guard.relative(absolutePath),
    bytes_written: Buffer.byteLength(input.content, "utf8"),
    status: "ok" as const,
  });
}

const LOG_DIR = path.join(".odessy", "logs");
const LOG_FILE = "odessy.log";

export async function logAppendTool(
  guard: WorkspaceGuard,
  input: { content: string },
): Promise<LogAppendResult> {
  const logDir = path.join(guard.root, LOG_DIR);
  await fs.promises.mkdir(logDir, { recursive: true });
  const logPath = path.join(logDir, LOG_FILE);
  const line = `[${new Date().toISOString()}] ${input.content}\n`;
  await fs.promises.appendFile(logPath, line, "utf8");
  return redactResult({
    path: guard.relative(logPath),
    bytes_written: Buffer.byteLength(line, "utf8"),
    status: "ok" as const,
  });
}

/**
 * Redact, retain and spill one git text field.
 *
 * The same ordering as terminal output and file content: redact first (a secret
 * must not reach the spill file), then keep both ends, then spill the rest. Git
 * output is unbounded -- a diff of a generated file is megabytes -- so without
 * this an enormous observation would be handed to the model unredacted and whole.
 */
function prepareGitText(result: GitTextResult, label: string, root: string): GitTextResult {
  const field = result.diff !== undefined ? "diff" : result.raw !== undefined ? "raw" : null;
  if (result.status !== "ok" || field === null) {
    return redactResult(result);
  }
  const prepared = prepareOutput(result[field] ?? "", {
    label,
    maxChars: GIT_TEXT_MAX_CHARS,
    spill: new SpillStore(root),
  });
  return {
    ...result,
    [field]: prepared.text,
    ...(prepared.spill ? { spill: prepared.spill } : {}),
  };
}

async function gitText(
  cwd: string,
  args: string[],
  field: "raw" | "diff",
): Promise<GitTextResult> {
  try {
    const { stdout } = await execFileAsync("git", args, {
      cwd,
      encoding: "utf8",
      maxBuffer: 10 * 1024 * 1024,
    });
    return field === "diff" ? { status: "ok", diff: stdout } : { status: "ok", raw: stdout };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return redactResult({ status: "failed" as const, error: message });
  }
}

async function runShellCommand(
  command: string,
  cwd: string,
  timeoutSeconds: number,
  confinement: Confinement | undefined,
  workspaceRoot: string,
): Promise<{
  stdout: string;
  stderr: string;
  exitCode: number | null;
  timedOut: boolean;
  sandbox: SandboxReport;
}> {
  const shell = process.platform === "win32" ? "powershell" : "/bin/sh";
  const shellArgs =
    process.platform === "win32"
      ? ["-NoProfile", "-NonInteractive", "-Command", command]
      : ["-lc", command];

  // Confinement happens before anything is spawned, and it throws rather than
  // falling back: a caller that asked for a boundary must never receive an
  // unconfined run instead, because the record would be indistinguishable from one
  // where the boundary held. The argv below is the one `confineArgv` returned.
  const confined = confineArgv(confinement, [shell, ...shellArgs], workspaceRoot);
  const [executable, ...args] = confined.argv;
  if (executable === undefined) {
    throw new Error("confinement returned an empty argv");
  }

  return await new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      cwd,
      env: buildSanitizedEnvironment(process.env),
      stdio: ["ignore", "pipe", "pipe"],
      detached: process.platform !== "win32",
      windowsHide: true,
      shell: false,
    });

    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;

    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr?.on("data", (chunk: string) => {
      stderr += chunk;
    });

    const finish = (result: {
      stdout: string;
      stderr: string;
      exitCode: number | null;
      timedOut: boolean;
    }) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ...result, sandbox: confined.sandbox });
    };

    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });

    child.on("exit", (code) => {
      finish({
        stdout,
        stderr,
        // `null` when there is no status to report. A process the executor killed has
        // no exit code of its own, and a signal name is not a process exit status
        // either, so neither is flattened into a number that would read as an outcome.
        exitCode: timedOut || typeof code !== "number" ? null : code,
        timedOut,
      });
    });

    const timer = setTimeout(() => {
      timedOut = true;
      terminateProcessTree(child);
    }, timeoutSeconds * 1000);
  });
}

function buildSanitizedEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const allowed = new Set([
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    // Match Python: honor the host's explicit PowerShell module search path.
    "PSMODULEPATH",
    // Let the host choose a writable module-analysis cache as well as its search path.
    "PSMODULEANALYSISCACHEPATH",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "WINDIR",
  ]);
  const result: NodeJS.ProcessEnv = {};
  for (const [key, value] of Object.entries(source)) {
    const upper = key.toUpperCase();
    if (allowed.has(upper) && !looksLikeSecretEnvName(upper) && typeof value === "string") {
      result[key] = value;
    }
  }
  return result;
}

function looksLikeSecretEnvName(name: string): boolean {
  const secretTerms = [
    "API_KEY",
    "AWS_",
    "AZURE_",
    "CREDENTIAL",
    "GITHUB_TOKEN",
    "HF_TOKEN",
    "HUGGINGFACE",
    "OPENAI_",
    "PASSWORD",
    "PRIVATE",
    "SECRET",
    "TOKEN",
  ];
  return secretTerms.some((term) => name.includes(term));
}

function terminateProcessTree(child: import("node:child_process").ChildProcess): void {
  if (child.exitCode !== null || child.signalCode !== null) {
    return;
  }
  if (process.platform === "win32") {
    spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    }).unref();
    return;
  }
  if (typeof child.pid === "number") {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      child.kill("SIGTERM");
    }
  }
}

async function walk(
  root: string,
  visit: (absolutePath: string, stat: fs.Stats) => Promise<void>,
): Promise<void> {
  const stat = await fs.promises.stat(root);
  await visit(root, stat);
  if (!stat.isDirectory()) return;
  const entries = await fs.promises.readdir(root);
  for (const entry of entries) {
    if (SKIP_DIRS.has(entry)) continue;
    await walk(path.join(root, entry), visit);
  }
}

function buildMatcher(
  pattern: string,
  options: { ignoreCase: boolean; literal: boolean },
): (line: string) => boolean {
  if (options.literal) {
    const needle = options.ignoreCase ? pattern.toLowerCase() : pattern;
    return (line) => (options.ignoreCase ? line.toLowerCase() : line).includes(needle);
  }
  const regex = new RegExp(pattern, options.ignoreCase ? "i" : undefined);
  return (line) => regex.test(line);
}

/**
 * Whether a file name matches a `project.find` pattern.
 *
 * A plain pattern is a case-insensitive substring of the name. A pattern with `*` or
 * `?` is a glob on the name: models write `*.py` and `**\/*.py`, and as substrings those
 * matched nothing, so a search that should list every Python file found none. Only the
 * last path segment counts, because names are matched, not paths.
 */
export function fileNameMatches(fileName: string, pattern: string): boolean {
  const name = fileName.toLowerCase();
  const needle = pattern.toLowerCase();
  if (!/[*?]/.test(needle)) return name.includes(needle);
  return globMatches(name, needle.split(/[\\/]/).pop() ?? needle);
}

function globMatches(filename: string, glob: string): boolean {
  const escaped = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replaceAll("*", ".*").replaceAll("?", ".");
  return new RegExp(`^${escaped}$`).test(filename);
}
