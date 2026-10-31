import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const UNICODE_SPACES = /[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g;
const SECRET_SEGMENTS = new Set([
  ".aws",
  ".azure",
  ".docker",
  ".env",
  ".git-credentials",
  ".gnupg",
  ".kube",
  ".netrc",
  ".npmrc",
  ".pypirc",
  ".ssh",
  "credentials.json",
  "id_dsa",
  "id_ecdsa",
  "id_ed25519",
  "id_rsa",
  "secrets.json",
  "token.json",
]);
/** A file named like this is a credential. Mirrors the policy's sensitive basenames. */
const SECRET_BASENAMES = /^\.env(\..*)?$|\.(key|pem)$|credential|secret|token/;

export interface WorkspaceGuard {
  root: string;
  resolveCandidate(candidate?: string): string;
  resolveExisting(candidate?: string): Promise<string>;
  relative(absolutePath: string): string;
}

function normalizeCandidate(candidate?: string): string {
  const raw = candidate && candidate.trim() ? candidate.trim() : ".";
  const withoutAtPrefix = raw.startsWith("@") ? raw.slice(1) : raw;
  const normalized = withoutAtPrefix.replace(UNICODE_SPACES, " ");
  if (normalized === "~") {
    return os.homedir();
  }
  if (normalized.startsWith("~/") || normalized.startsWith("~\\")) {
    return path.join(os.homedir(), normalized.slice(2));
  }
  return normalized;
}

function isContainedPath(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return (
    relative === "" ||
    (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))
  );
}

function assertNotSecretPath(root: string, absolutePath: string): void {
  const relative = path.relative(root, absolutePath);
  const parts = relative.split(/[\\/]+/).filter(Boolean);
  for (const part of parts) {
    if (SECRET_SEGMENTS.has(part.toLowerCase())) {
      throw new Error(`Odessy denied access to protected path segment: ${part}`);
    }
  }
  const basename = parts.at(-1)?.toLowerCase();
  if (basename !== undefined && SECRET_BASENAMES.test(basename)) {
    throw new Error(`Odessy denied access to protected path segment: ${basename}`);
  }
}

export function createWorkspaceGuard(workspaceRoot: string): WorkspaceGuard {
  const lexicalRoot = path.resolve(workspaceRoot);
  // Native, to match `fs.promises.realpath` below. The JavaScript `realpathSync`
  // keeps Windows 8.3 short names (`RUNNER~1`) that the native call expands, so the
  // workspace root would otherwise not contain itself.
  const realRoot = fs.realpathSync.native(lexicalRoot);

  function resolveCandidate(candidate = "."): string {
    const normalized = normalizeCandidate(candidate);
    const lexicalPath = path.isAbsolute(normalized)
      ? path.resolve(normalized)
      : path.resolve(lexicalRoot, normalized);
    if (!isContainedPath(lexicalRoot, lexicalPath) && !isContainedPath(realRoot, lexicalPath)) {
      throw new Error(`Odessy path is outside the workspace: ${candidate}`);
    }
    assertNotSecretPath(lexicalRoot, lexicalPath);
    return lexicalPath;
  }

  return {
    root: realRoot,
    resolveCandidate,
    async resolveExisting(candidate = ".") {
      const lexicalPath = resolveCandidate(candidate);
      const realPath = await fs.promises.realpath(lexicalPath);
      if (!isContainedPath(realRoot, realPath)) {
        throw new Error(`Odessy path resolves outside the workspace: ${candidate}`);
      }
      assertNotSecretPath(realRoot, realPath);
      if (realPath.replace(UNICODE_SPACES, " ") !== realPath) {
        throw new Error(`Odessy path is not stable after normalization: ${candidate}`);
      }
      return realPath;
    },
    relative(absolutePath: string) {
      // resolveCandidate returns lexical paths and resolveExisting real ones, and the
      // two roots can differ (a symlinked root, a Windows 8.3 short name), so measure
      // from whichever root actually contains the path.
      const base =
        !isContainedPath(realRoot, absolutePath) && isContainedPath(lexicalRoot, absolutePath)
          ? lexicalRoot
          : realRoot;
      const relative = path.relative(base, absolutePath).replaceAll(path.sep, "/");
      return relative || ".";
    },
  };
}
