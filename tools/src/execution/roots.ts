/**
 * Canonical path resolution and the writable-root derivation.
 *
 * Moved from `dsh-sandbox/src/roots.ts`, which is the one module in that package
 * with no external imports at all.
 *
 * `canonicalPath` delegates to Node's *native* realpath deliberately. Node's
 * JavaScript implementation lexically collapses `..` before resolving a
 * preceding symlink, so `link/..` would resolve to the link's own parent rather
 * than the parent of its target. The Python implementation cannot delegate to
 * its standard library for the same reason and resolves component by component
 * instead; this side gets the correct behaviour from the runtime.
 */

import { existsSync, realpathSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import path from "node:path";

import type { ExecutionPolicy } from "./contract.js";

/**
 * Resolve a granted root to the path the enforcement layer actually compares.
 *
 * Canonical, because path-matching enforcement layers match resolved paths:
 * `/tmp` *is* `/private/tmp` on macOS, and an as-spelled grant would match
 * nothing.
 *
 * A path that does not exist returns its spelling as-is, matching upstream and
 * the Python implementation. That is the conservative outcome: a root that does
 * not exist matches nothing until it does, and inventing a fallback would grant
 * a path the caller never named.
 */
export function canonicalPath(path: string): string {
  try {
    return realpathSync.native(path);
  } catch {
    return path;
  }
}

/**
 * The roots one confined execution may *write* under.
 *
 * `read-only` allows nothing. `workspace-write` allows the policy's workspace
 * root, the host `/tmp`, and the platform temp directory — the real temp area
 * for `mkstemp`-family tools, which omitting would deny what the mode promises.
 *
 * Canonicalized then deduplicated in first-seen order. That dedup is what
 * collapses `/tmp` and `/private/tmp` to one entry on macOS.
 */
export function writableRoots(policy: ExecutionPolicy): string[] {
  if (policy.mode !== "workspace-write") {
    return [];
  }
  return [
    ...new Set(
      [policy.workspaceRoot, "/tmp", tmpdir(), ...(policy.extraWritable ?? [])].map(canonicalPath),
    ),
  ];
}

function inside(child: string, parent: string): boolean {
  const relative = path.relative(parent, child);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

/**
 * The paths to hide from one confined run, from patterns the authority layer sent.
 *
 * `~` is this host's home. A path that does not exist is dropped (nothing to hide),
 * and so is one that contains the workspace or lies inside it: hiding the workspace
 * would break the run, and a workspace file is the workspace's business. Same rule
 * as the Python `hidden_roots`.
 */
export function hiddenRoots(patterns: readonly string[], workspaceRoot: string): string[] {
  const workspace = canonicalPath(workspaceRoot);
  const roots: string[] = [];
  for (const pattern of patterns) {
    const expanded =
      pattern === "~" || pattern.startsWith("~/") || pattern.startsWith("~\\")
        ? path.join(homedir(), pattern.slice(1))
        : pattern;
    if (!path.isAbsolute(expanded) || !existsSync(expanded)) continue;
    const resolved = canonicalPath(expanded);
    if (inside(workspace, resolved) || inside(resolved, workspace)) continue;
    if (!roots.includes(resolved)) roots.push(resolved);
  }
  return roots;
}

/**
 * Extra writable folders from the authority layer: absolute after `~` expansion,
 * existing, and never a filesystem root or the home folder itself.
 */
export function extraWritableRoots(patterns: readonly string[]): string[] {
  const home = canonicalPath(homedir());
  const roots: string[] = [];
  for (const pattern of patterns) {
    const expanded = pattern.startsWith("~/") || pattern.startsWith("~\\") ? path.join(homedir(), pattern.slice(2)) : pattern;
    if (!path.isAbsolute(expanded) || !existsSync(expanded)) continue;
    const resolved = canonicalPath(expanded);
    if (resolved === home || path.dirname(resolved) === resolved) continue;
    if (!roots.includes(resolved)) roots.push(resolved);
  }
  return roots;
}
