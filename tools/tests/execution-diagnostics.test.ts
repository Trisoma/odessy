import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import type { RunnerFailureRule } from "../src/execution/contract.js";
import {
  classifyRunnerFailure,
  isRunnerSpawnFailure,
  matchesSignature,
} from "../src/execution/diagnostics.js";

// Real backend dialects, as the providers declare them.
const BWRAP: RunnerFailureRule = { fatalSignatures: ["bwrap: "] };
const LANDLOCK: RunnerFailureRule = {
  fatalSignatures: ["landlock-run: "],
  allowedExitCodes: [125],
  informationalLines: ["landlock-run: partial enforcement (older Landlock ABI)"],
};
const SEATBELT: RunnerFailureRule = { fatalSignatures: ["sandbox-exec: "] };
const WINDOWS_ACL: RunnerFailureRule = {
  fatalSignatures: ["windows-acl-run: "],
  allowedExitCodes: [127],
};

test("a zero exit is never a runner failure", () => {
  for (const rule of [BWRAP, LANDLOCK, SEATBELT, WINDOWS_ACL]) {
    assert.equal(classifyRunnerFailure(0, "bwrap: anything\n", [rule]), undefined);
  }
});

test("a signal termination is never a runner failure", () => {
  // null means the process was signalled, not that the runner failed.
  assert.equal(classifyRunnerFailure(null, "bwrap: anything\n", [BWRAP]), undefined);
});

test("a fatal signature is matched and reported", () => {
  const match = classifyRunnerFailure(1, "bwrap: Creating new namespace failed\n", [BWRAP]);

  assert.equal(match?.detail, "bwrap: Creating new namespace failed");
});

test("matching is case insensitive", () => {
  assert.notEqual(classifyRunnerFailure(1, "BWRAP: Failed\n", [BWRAP]), undefined);
});

test("the exit code gate excludes other codes", () => {
  assert.equal(classifyRunnerFailure(1, "landlock-run: boom\n", [LANDLOCK]), undefined);
  assert.notEqual(classifyRunnerFailure(125, "landlock-run: boom\n", [LANDLOCK]), undefined);
});

test("the informational line is excluded before fatal matching", () => {
  // The older-ABI notice contains the fatal prefix but is benign. Without
  // exact-line exclusion a working sandbox would be reported as broken.
  const stderr = "landlock-run: partial enforcement (older Landlock ABI)\n";

  assert.equal(classifyRunnerFailure(125, stderr, [LANDLOCK]), undefined);
});

test("informational exclusion is exact, not substring", () => {
  const stderr = "landlock-run: partial enforcement (older Landlock ABI) and then failed\n";

  assert.notEqual(classifyRunnerFailure(125, stderr, [LANDLOCK]), undefined);
});

test("a blank fatal signature is ignored and does not disable a valid sibling", () => {
  assert.equal(classifyRunnerFailure(1, "anything\n", [{ fatalSignatures: ["  "] }]), undefined);
  assert.notEqual(
    classifyRunnerFailure(1, "bwrap: boom\n", [{ fatalSignatures: ["   ", "bwrap: "] }]),
    undefined,
  );
});

test("matchesSignature requires a non-zero exit", () => {
  assert.equal(matchesSignature(0, "read-only file system", ["read-only file system"]), false);
  assert.equal(matchesSignature(null, "read-only file system", ["read-only file system"]), false);
});

test("matchesSignature is case insensitive", () => {
  assert.equal(matchesSignature(1, "Read-Only File System", ["read-only file system"]), true);
});

test("matchesSignature rejects an unrelated dialect", () => {
  // The selected backend's dialect only, never a cross-backend union.
  assert.equal(matchesSignature(1, "permission denied", ["read-only file system"]), false);
});

test("matchesSignature accepts every signature in the selected dialect", () => {
  const dialect = [
    "access is denied",
    "access to the path",
    "permission denied",
    "operation not permitted",
  ];
  for (const text of dialect) {
    assert.equal(matchesSignature(1, `error: ${text}`, dialect), true);
  }
});

test("spawn failure needs a usable workdir", () => {
  // An unusable cwd produces the same errno as a missing executable.
  const missing = path.join(os.tmpdir(), "odessy-does-not-exist-workdir");
  const error = { code: "ENOENT", path: "/usr/bin/bwrap", syscall: "spawn /usr/bin/bwrap" };

  assert.equal(isRunnerSpawnFailure(error, "/usr/bin/bwrap", missing), false);
});

test("spawn failure attributes a matching path", () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "odessy-diag-"));
  const error = { code: "ENOENT", path: "/usr/bin/bwrap", syscall: "spawn /usr/bin/bwrap" };

  assert.equal(isRunnerSpawnFailure(error, "/usr/bin/bwrap", workdir), true);
});

test("spawn failure rejects a path that is not the runner", () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "odessy-diag-"));
  const error = { code: "ENOENT", path: "/etc/passwd", syscall: "spawn /usr/bin/bwrap" };

  assert.equal(isRunnerSpawnFailure(error, "/usr/bin/bwrap", workdir), false);
});

test("spawn failure without a path requires the exact syscall", () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "odessy-diag-"));

  assert.equal(
    isRunnerSpawnFailure({ code: "ENOENT", syscall: "spawn /usr/bin/bwrap" }, "/usr/bin/bwrap", workdir),
    true,
  );
  assert.equal(
    isRunnerSpawnFailure({ code: "ENOENT", syscall: "spawn /bin/sh" }, "/usr/bin/bwrap", workdir),
    false,
  );
});

test("spawn failure accepts EACCES too", () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "odessy-diag-"));
  const error = { code: "EACCES", path: "/usr/bin/bwrap", syscall: "spawn /usr/bin/bwrap" };

  assert.equal(isRunnerSpawnFailure(error, "/usr/bin/bwrap", workdir), true);
});

test("spawn failure ignores unrelated codes and shapes", () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "odessy-diag-"));

  assert.equal(
    isRunnerSpawnFailure({ code: "EINVAL", path: "/usr/bin/bwrap", syscall: "spawn /usr/bin/bwrap" }, "/usr/bin/bwrap", workdir),
    false,
  );
  assert.equal(isRunnerSpawnFailure(new Error("boom"), "/usr/bin/bwrap", workdir), false);
  assert.equal(isRunnerSpawnFailure(null, "/usr/bin/bwrap", workdir), false);
});

test("spawn failure needs a runner program", () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "odessy-diag-"));

  assert.equal(isRunnerSpawnFailure({ code: "ENOENT" }, undefined, workdir), false);
});
