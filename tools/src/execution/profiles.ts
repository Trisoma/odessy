/**
 * Profile argument construction for each same-world backend.
 *
 * Moved from `dsh-sandbox-local/src/profiles.ts`. The bwrap and Seatbelt builders
 * are upstream's, verbatim. The Landlock builder emits the launcher's documented
 * flag vocabulary directly rather than calling a Node helper; the grammar is
 * pinned by `native/system/docs/cli-contract.md`:
 *
 * ```text
 * landlock-run [--ro <path>]... [--rw <path>]... -- <argv>...
 * ```
 *
 * Every builder is pure, so the profiles are directly testable and this stays the
 * one place a grant is spelled.
 *
 * Each refuses `danger-full-access`. A confining profile for a mode that requests
 * no confinement is a caller error, and silently producing one would mean a
 * caller could ask for full access and receive confinement, or believe the
 * reverse.
 */

import { statSync } from "node:fs";

import { isConfinedMode, type ExecutionPolicy } from "./contract.js";
import { writableRoots } from "./roots.js";

function isDirectory(target: string): boolean {
  try {
    return statSync(target).isDirectory();
  } catch {
    return false;
  }
}

function requireConfined(policy: ExecutionPolicy, backend: string): void {
  if (!isConfinedMode(policy.mode)) {
    throw new Error(
      `${backend} was asked for a profile under mode ${JSON.stringify(policy.mode)}, which ` +
        "requests no confinement; only read-only and workspace-write have a profile",
    );
  }
}

/**
 * The bwrap mounts for one policy.
 *
 * A read-only host root, a *fresh* `/dev`, a private PID namespace, and `/proc`
 * mounted from it. The fresh `/dev` matters for more than hygiene: it shadows the
 * host's device nodes, so a confined process cannot see a serial port, camera, or
 * GPIO chip at all. Device access is restricted by absence rather than by
 * permission.
 */
export function bwrapProfileArgs(policy: ExecutionPolicy): string[] {
  requireConfined(policy, "bwrap");

  const args = [
    "--ro-bind",
    "/",
    "/",
    "--dev",
    "/dev",
    "--unshare-pid",
    "--proc",
    "/proc",
    "--die-with-parent",
  ];
  // Network: a private namespace with only loopback. Hidden paths: a folder is
  // covered by an empty tmpfs, a file by /dev/null, before the writable binds.
  if (policy.network === "deny") {
    args.push("--unshare-net");
  }
  for (const hidden of policy.hiddenPaths ?? []) {
    if (isDirectory(hidden)) {
      args.push("--tmpfs", hidden);
    } else {
      args.push("--ro-bind", "/dev/null", hidden);
    }
  }
  if (policy.mode === "workspace-write") {
    args.push("--tmpfs", "/tmp");
    args.push("--bind", policy.workspaceRoot, policy.workspaceRoot);
    for (const extra of policy.extraWritable ?? []) {
      args.push("--bind", extra, extra);
    }
  }
  return args;
}

/**
 * The Landlock launcher grants for one policy.
 *
 * Landlock rulesets are allow-lists, so everything not granted is denied.
 * `--ro /` grants read and execute across the host; `--rw /dev/null` is the sink
 * the wrapped shell requires (a grant on a non-directory keeps only its
 * file-compatible access bits, which is how that works).
 */
export function landlockProfileArgs(policy: ExecutionPolicy): string[] {
  requireConfined(policy, "landlock");

  const args = ["--ro", "/", "--rw", "/dev/null"];
  if (policy.mode === "workspace-write") {
    args.push("--rw", "/tmp", "--rw", policy.workspaceRoot);
    for (const extra of policy.extraWritable ?? []) {
      args.push("--rw", extra);
    }
  }
  return args;
}

/** Quote one path as an SBPL string literal. */
function sbplString(path: string): string {
  // Backslash first, then quote: escaping the quote first would corrupt a path
  // already containing a backslash.
  return `"${path.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

/**
 * The `sandbox-exec` arguments for one policy.
 *
 * Allow-default with `file-write*` denied, then exactly the mode's promised write
 * effects allowed back: the `/dev/null` sink and the canonical writable roots.
 * The roots come from the shared `writableRoots` helper so this profile and any
 * other consumer cannot drift apart and produce a "the write tool cannot write
 * there but the shell can" asymmetry.
 */
export function seatbeltProfileArgs(policy: ExecutionPolicy): string[] {
  requireConfined(policy, "seatbelt");

  const forms = [
    "(version 1)",
    "(allow default)",
    "(deny file-write*)",
    `(allow file-write* (literal ${sbplString("/dev/null")}))`,
  ];
  if (policy.network === "deny") {
    forms.push("(deny network*)");
  }
  for (const hidden of policy.hiddenPaths ?? []) {
    forms.push(`(deny file-read* (${isDirectory(hidden) ? "subpath" : "literal"} ${sbplString(hidden)}))`);
  }

  const roots = writableRoots(policy);
  if (roots.length > 0) {
    const subpaths = roots.map((root) => `(subpath ${sbplString(root)})`).join(" ");
    forms.push(`(allow file-write* ${subpaths})`);
  }

  return ["-p", forms.join(" ")];
}

/** The full argv to spawn: runner, profile, separator, caller's command. */
export function bwrapCommand(
  executable: string,
  argv: readonly string[],
  policy: ExecutionPolicy,
): string[] {
  return [executable, ...bwrapProfileArgs(policy), "--", ...argv];
}

export function landlockCommand(
  executable: string,
  argv: readonly string[],
  policy: ExecutionPolicy,
): string[] {
  return [executable, ...landlockProfileArgs(policy), "--", ...argv];
}

export function seatbeltCommand(
  executable: string,
  argv: readonly string[],
  policy: ExecutionPolicy,
): string[] {
  return [executable, ...seatbeltProfileArgs(policy), "--", ...argv];
}
