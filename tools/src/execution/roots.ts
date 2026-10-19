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

import { realpathSync } from "node:fs";
import { tmpdir } from "node:os";

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
  return [...new Set([policy.workspaceRoot, "/tmp", tmpdir()].map(canonicalPath))];
}
