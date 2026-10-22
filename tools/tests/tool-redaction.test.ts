/**
 * Every tool result is redacted before it leaves the tool server.
 *
 * `terminal.run` already went through `output/prepare.ts`; the other tools returned
 * whatever they had read, diffed or grepped. These tests pin the two guarantees the
 * change makes:
 *
 * - every returned string is redacted, at any depth, including captured file content
 *   and git text;
 * - captured text keeps the pipeline's order — redact, then retain, then spill — so
 *   the spill file on disk never contains the secret either.
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { redactResult } from "../src/output/prepare.js";
import {
  gitDiff,
  gitStatus,
  grepProjectTool,
  listDirectoryTool,
  readFileTool,
} from "../src/tools/index.js";
import { createWorkspaceGuard } from "../src/workspace/path-guard.js";

const SECRET = "sk-abcdefghijklmnopqrstuvwxyz";

function makeWorkspace(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "odessy-redaction-"));
}

// --------------------------------------------------------------------------
// The shared walk
// --------------------------------------------------------------------------

test("redactResult walks nested results and keeps their shape", () => {
  const result = {
    status: "ok",
    content: `API_KEY=${SECRET}`,
    matches: [{ path: "a.env", text: `TOKEN=${SECRET}` }],
    count: 1,
    truncated: false,
  };

  const redacted = redactResult(result);

  assert.equal(redacted.status, "ok");
  assert.equal(redacted.count, 1);
  assert.equal(redacted.truncated, false);
  assert.doesNotMatch(JSON.stringify(redacted), new RegExp(SECRET));
  assert.match(redacted.content, /<redacted>/);
  assert.match(redacted.matches[0]!.text, /<redacted>/);
});

test("redactResult does not rewrite the caller's object", () => {
  const result = { content: `API_KEY=${SECRET}` };

  redactResult(result);

  assert.equal(result.content, `API_KEY=${SECRET}`);
});

test("redactResult leaves non-string scalars alone", () => {
  const result = { exit_code: 0, duration: 1.5, timed_out: false };

  assert.deepEqual(redactResult(result), result);
});

// --------------------------------------------------------------------------
// Credential-named fields
// --------------------------------------------------------------------------

test("a credential-named field is redacted whatever its value looks like", () => {
  // An opaque value under `password` is neither a `NAME=value` assignment nor a
  // known token shape, so a values-only walk would let it through.
  const result = {
    password: "correct-horse-battery-staple",
    api_key: "opaque-provider-credential",
    nested: { client_secret: "another-opaque-value" },
    list: [{ token: "also-opaque" }],
    path: ".env",
  };

  const redacted = redactResult(result);

  assert.equal(redacted.password, "<redacted>");
  assert.equal(redacted.api_key, "<redacted>");
  assert.equal(redacted.nested.client_secret, "<redacted>");
  assert.equal(redacted.list[0]!.token, "<redacted>");
  // A non-credential field must survive, or the model cannot act on the result.
  assert.equal(redacted.path, ".env");
});

test("a credential-named field keeps its nested structure", () => {
  const redacted = redactResult({ credentials: { user: "alice", pass: "hunter2" } });

  assert.deepEqual(redacted, { credentials: { user: "<redacted>", pass: "<redacted>" } });
});

// --------------------------------------------------------------------------
// filesystem.read: captured content goes through the whole pipeline
// --------------------------------------------------------------------------

test("filesystem.read redacts a secret in the file content", async () => {
  const workspace = makeWorkspace();
  fs.writeFileSync(path.join(workspace, "config.txt"), `OPENAI_API_KEY=${SECRET}\n`, "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await readFileTool(guard, { path: "config.txt" });

  assert.doesNotMatch(result.content, new RegExp(SECRET));
  assert.match(result.content, /<redacted>/);
  assert.equal(result.truncated, false);
});

test("filesystem.read spills redacted content, never the secret", async () => {
  const workspace = makeWorkspace();
  const body = `API_KEY=${SECRET}\n${"line of log output\n".repeat(400)}`;
  fs.writeFileSync(path.join(workspace, "big.txt"), body, "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await readFileTool(guard, { path: "big.txt", max_bytes: 400 });

  assert.equal(result.truncated, true);
  assert.ok(result.spill, "a truncated read must leave a recoverable copy");
  // Both ends are kept, and the notice says where the rest is.
  assert.match(result.content, /truncated/);
  assert.match(result.content, new RegExp(result.spill.relative_path));

  const spilled = fs.readFileSync(result.spill.path, "utf8");
  assert.doesNotMatch(spilled, new RegExp(SECRET), "the spill file must not hold the secret");
  assert.match(spilled, /<redacted>/);
  // Everything is recoverable from the spill file.
  assert.ok(spilled.length >= body.length - 40);
});

// --------------------------------------------------------------------------
// Other read-only tools
// --------------------------------------------------------------------------

test("project.grep redacts the matched line", async () => {
  const workspace = makeWorkspace();
  fs.writeFileSync(path.join(workspace, "config.txt"), `OPENAI_API_KEY=${SECRET}\n`, "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await grepProjectTool(guard, { pattern: "OPENAI_API_KEY" });

  assert.equal(result.count, 1);
  assert.doesNotMatch(JSON.stringify(result), new RegExp(SECRET));
  assert.match(result.matches[0]!.text, /<redacted>/);
});

test("project.grep redacts before truncating a match line", async () => {
  // A token cut in half matches no pattern, so truncating first left its prefix in
  // the output. These paddings put the cut inside the secret.
  for (const padding of [480, 495]) {
    const workspace = makeWorkspace();
    const line = `${"x".repeat(padding)} API_KEY=${SECRET}`;
    fs.writeFileSync(path.join(workspace, "config.txt"), `${line}\n`, "utf8");
    const guard = createWorkspaceGuard(workspace);

    const result = await grepProjectTool(guard, { pattern: "API_KEY" });

    const text = result.matches[0]!.text;
    assert.ok(text.length <= 500, `padding ${padding}: ${text.length} chars`);
    assert.ok(!text.includes("sk-"), `padding ${padding} leaked a secret prefix: ${text.slice(-20)}`);
  }
});

test("filesystem.list redacts names that contain a known token shape", async () => {
  const workspace = makeWorkspace();
  fs.writeFileSync(path.join(workspace, `note-${SECRET}.txt`), "x", "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await listDirectoryTool(guard, { path: "." });

  assert.doesNotMatch(JSON.stringify(result), new RegExp(SECRET));
});

// --------------------------------------------------------------------------
// git text: unbounded captured output
// --------------------------------------------------------------------------

function makeGitWorkspace(): string | null {
  const workspace = makeWorkspace();
  try {
    execFileSync("git", ["init"], { cwd: workspace, stdio: "ignore" });
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: workspace });
    execFileSync("git", ["config", "user.name", "Odessy Test"], { cwd: workspace });
    fs.writeFileSync(path.join(workspace, "tracked.txt"), "clean\n", "utf8");
    execFileSync("git", ["add", "tracked.txt"], { cwd: workspace });
    execFileSync("git", ["commit", "-m", "initial"], { cwd: workspace, stdio: "ignore" });
    fs.writeFileSync(path.join(workspace, "tracked.txt"), `API_KEY=${SECRET}\n`, "utf8");
    return workspace;
  } catch {
    return null;
  }
}

test("git.diff redacts a secret added to a tracked file", async (t) => {
  const workspace = makeGitWorkspace();
  if (workspace === null) {
    t.skip("git is not available");
    return;
  }
  const guard = createWorkspaceGuard(workspace);

  const result = await gitDiff(guard, { mode: "working" });

  assert.equal(result.status, "ok");
  assert.ok(result.diff);
  assert.doesNotMatch(result.diff, new RegExp(SECRET));
  assert.match(result.diff, /<redacted>/);
});

test("git.status redacts a secret in a tracked path", async (t) => {
  const workspace = makeGitWorkspace();
  if (workspace === null) {
    t.skip("git is not available");
    return;
  }
  fs.writeFileSync(path.join(workspace, `secret-${SECRET}.txt`), "x", "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await gitStatus(guard);

  assert.equal(result.status, "ok");
  assert.doesNotMatch(result.raw ?? "", new RegExp(SECRET));
});
