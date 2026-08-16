import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { z } from "zod";

import type {
  FilesystemListResult,
  FilesystemReadResult,
  GitChangedFilesResult,
  GitLogResult,
  GitTextResult,
  ProjectFindResult,
  ProjectGrepMatch,
  ProjectGrepResult,
} from "../output/contracts.js";
import { DEFAULT_MAX_BYTES, normalizeLimit, truncateUtf8 } from "../output/truncate.js";
import type { WorkspaceGuard } from "../workspace/path-guard.js";

const execFileAsync = promisify(execFile);
const SKIP_DIRS = new Set([".git", "node_modules", "__pycache__", ".pytest_cache", "dist"]);

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
  const truncated = truncateUtf8(raw, input.max_bytes ?? DEFAULT_MAX_BYTES);
  return {
    path: guard.relative(absolutePath),
    content: truncated.content,
    size: stat.size,
    status: "ok",
    truncated: truncated.truncated,
    ...(truncated.truncation ? { truncation: truncated.truncation } : {}),
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
  return {
    path: guard.relative(absolutePath),
    entries,
    status: "ok",
    truncated: names.length > visible.length,
  };
}

export async function findProjectTool(
  guard: WorkspaceGuard,
  input: { pattern: string; path?: string; limit?: number },
): Promise<ProjectFindResult> {
  const root = await guard.resolveExisting(input.path ?? ".");
  const limit = normalizeLimit(input.limit);
  const needle = input.pattern.toLowerCase();
  const matches: string[] = [];

  await walk(root, async (absolutePath, stat) => {
    if (matches.length >= limit) return;
    if (stat.isFile() && path.basename(absolutePath).toLowerCase().includes(needle)) {
      matches.push(guard.relative(absolutePath));
    }
  });

  return {
    pattern: input.pattern,
    path: guard.relative(root),
    matches,
    count: matches.length,
    status: "ok",
    truncated: matches.length >= limit,
  };
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
          text: truncateUtf8(lines[index] ?? "", 500).content,
        });
      }
    }
  });

  return {
    pattern: input.pattern,
    path: guard.relative(root),
    matches,
    count: matches.length,
    status: "ok",
    truncated: matches.length >= limit,
  };
}

export async function gitStatus(guard: WorkspaceGuard): Promise<GitTextResult> {
  return gitText(guard.root, ["status", "--porcelain=v1", "--branch"], "raw");
}

export async function gitDiff(
  guard: WorkspaceGuard,
  input: { mode?: "working" | "cached" },
): Promise<GitTextResult> {
  const args =
    input.mode === "cached"
      ? ["diff", "--cached", "--no-ext-diff"]
      : ["diff", "--no-ext-diff"];
  return gitText(guard.root, args, "diff");
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
      return { status: "failed", files: [], error: result.error };
    }
    for (const line of (result.raw ?? "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean)) {
      files.add(line.replaceAll("\\", "/"));
    }
  }
  return { status: "ok", files: [...files].sort() };
}

export async function gitLog(
  guard: WorkspaceGuard,
  input: { limit?: number },
): Promise<GitLogResult> {
  const limit = Math.max(1, Math.min(Math.floor(input.limit ?? 20), 50));
  const result = await gitText(guard.root, ["log", "--oneline", "-n", String(limit)], "raw");
  if (result.status !== "ok") {
    return { status: "failed", commits: [], limit, error: result.error };
  }
  return {
    status: "ok",
    commits: (result.raw ?? "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
    limit,
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
    return { status: "failed", error: message };
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

function globMatches(filename: string, glob: string): boolean {
  const escaped = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replaceAll("*", ".*").replaceAll("?", ".");
  return new RegExp(`^${escaped}$`).test(filename);
}
