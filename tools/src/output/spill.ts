/**
 * Local spill storage: keep withheld output recoverable.
 *
 * TypeScript mirror of `odessy/spill.py`. The property that matters is preserved
 * exactly: **spilling never destroys.** If a write fails, `saveText` returns
 * `null` and the caller keeps the original content. There is no path in this
 * module that converts a storage failure into data loss.
 *
 * Location is inside the workspace root, under `.odessy/spill/<session>/`. That is
 * deliberate: the workspace authority already bounds every path to the workspace,
 * so putting spills there means retrieval inherits that boundary for free instead
 * of needing a second, weaker one.
 *
 * **Retrieval is deliberately not ported.** Python's `read_text` recovers a
 * payload through `WorkspaceAuthority`; there is no consumer for it on this side,
 * and inventing a second path-validation implementation to serve one would be
 * building an abstraction with no demonstrated caller.
 */

import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

/**
 * Spill root, relative to the workspace root.
 *
 * Dot-prefixed so tooling treats it as metadata rather than content.
 */
export const SPILL_DIRNAME = ".odessy/spill";

/**
 * Filename components are reduced to this alphabet, so no caller-supplied label
 * can introduce a separator, a `..` segment, or a drive-relative path.
 */
const SAFE_COMPONENT = /[^a-z0-9._-]+/g;

/**
 * Digest prefix length.
 *
 * 16 hex characters is far past collision risk for the number of spills one
 * machine will ever hold, and stays readable.
 */
const DIGEST_CHARS = 16;

/** Narrow a caller-supplied string to a safe single path component. */
function safeComponent(value: string, fallback: string): string {
  const cleaned = value
    .trim()
    .toLowerCase()
    .replace(SAFE_COMPONENT, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
  return cleaned.slice(0, 64) || fallback;
}

/**
 * Write `payload` to `target` so a partial file is never visible.
 *
 * A *random* temp name is used rather than a predictable one such as
 * `<target>.<pid>.tmp`. A per-process name collides when the same content is
 * spilled twice in one process — the common case, since identical output resolves
 * to the same content-addressed target — and on Windows that collision surfaces
 * as a transient error that would make a *successful* second write report failure.
 *
 * One retry covers Windows sharing violations, which are transient by nature.
 */
function writeAtomically(target: string, payload: Buffer): void {
  const tempName = path.join(
    path.dirname(target),
    `.${path.basename(target)}.${randomBytes(6).toString("hex")}.tmp`,
  );

  try {
    const handle = fs.openSync(tempName, "w");
    try {
      fs.writeSync(handle, payload);
      // Without the fsync the rename can land before the bytes do.
      fs.fsyncSync(handle);
    } finally {
      fs.closeSync(handle);
    }

    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        fs.renameSync(tempName, target);
        return;
      } catch (error) {
        if (attempt === 1) {
          throw error;
        }
      }
    }
  } catch (error) {
    // Never leave the temp file behind on a failed write.
    try {
      fs.unlinkSync(tempName);
    } catch {
      // The temp file is already gone, which is the outcome we wanted.
    }
    throw error;
  }
}

/**
 * Enough metadata to identify and recover a spilled payload.
 *
 * Carries both an absolute and a workspace-relative path: the relative form is
 * what goes to the model (no host paths in the conversation), the absolute form is
 * what retrieval would validate.
 */
export interface SpillLocator {
  readonly path: string;
  readonly relative_path: string;
  readonly byte_count: number;
  readonly line_count: number;
  readonly digest: string;
}

export function spillLocatorAsDict(locator: SpillLocator): Record<string, unknown> {
  return {
    path: locator.path,
    relative_path: locator.relative_path,
    byte_count: locator.byte_count,
    line_count: locator.line_count,
    digest: locator.digest,
  };
}

/**
 * Count lines the way Python's `splitlines()` does.
 *
 * The difference that matters is the trailing newline: `"a\nb\n"` is two lines,
 * not three, so a trailing empty element is dropped rather than counted.
 */
function lineCount(text: string): number {
  if (text === "") {
    return 0;
  }
  const parts = text.split(/\r\n|[\n\r]/);
  if (parts[parts.length - 1] === "") {
    parts.pop();
  }
  return parts.length;
}

/** Content-addressed local spill storage inside the workspace boundary. */
export class SpillStore {
  readonly workspaceRoot: string;
  readonly sessionId: string;

  constructor(workspaceRoot: string, sessionId?: string) {
    this.workspaceRoot = path.resolve(workspaceRoot);
    // Odessy runs one bounded turn per process, so the process is the session.
    // Cheap, and it makes cleanup a matter of deleting a directory.
    const session = sessionId ?? `${process.pid}-${Math.floor(Date.now() / 1000)}`;
    this.sessionId = safeComponent(session, "session");
  }

  get directory(): string {
    return path.join(this.workspaceRoot, SPILL_DIRNAME, this.sessionId);
  }

  /**
   * Persist `text` and return its locator, or `null` if storage failed.
   *
   * `null` is the honest failure signal: the caller must then keep the original
   * content rather than substituting a lossy truncation.
   */
  saveText(text: string, label = "output"): SpillLocator | null {
    // `Buffer.from(..., 'utf8')` substitutes unpaired surrogates rather than
    // throwing, which is the recoverable behaviour the Python original reaches by
    // catching `UnicodeEncodeError` and re-encoding with `errors="replace"`.
    const payload = Buffer.from(text, "utf8");

    const digest = createHash("sha256").update(payload).digest("hex");
    const safeLabel = safeComponent(label, "output");
    const filename = `${safeLabel}-${digest.slice(0, DIGEST_CHARS)}-${payload.length}.txt`;
    const target = path.join(this.directory, filename);

    try {
      fs.mkdirSync(this.directory, { recursive: true });
      writeAtomically(target, payload);
    } catch {
      return null;
    }

    const relative = path.relative(this.workspaceRoot, target);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      return null;
    }

    return {
      path: target,
      // `relative_to(...).as_posix()` is the POSIX form regardless of host, and
      // this is the field that reaches the model.
      relative_path: relative.split(path.sep).join("/"),
      byte_count: payload.length,
      line_count: lineCount(text),
      digest: digest.slice(0, DIGEST_CHARS),
    };
  }
}

/** Build a store bound to a workspace root. */
export function spillStoreFor(workspaceRoot: string): SpillStore {
  return new SpillStore(workspaceRoot);
}
