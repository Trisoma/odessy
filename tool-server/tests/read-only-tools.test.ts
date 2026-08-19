import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  gitChangedFiles,
  gitDiff,
  gitLog,
  gitStatus,
  laptopTerminalRun,
  readFileTool,
  listDirectoryTool,
  findProjectTool,
  grepProjectTool,
} from "../src/tools/index.js";
import { createWorkspaceGuard } from "../src/workspace/path-guard.js";

function makeWorkspace(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "odessy-tools-"));
}

function makeGitWorkspace(): string | null {
  const workspace = makeWorkspace();
  try {
    execFileSync("git", ["init"], { cwd: workspace, stdio: "ignore" });
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: workspace });
    execFileSync("git", ["config", "user.name", "Odessy Test"], { cwd: workspace });
    fs.writeFileSync(path.join(workspace, "tracked.txt"), "one\n", "utf8");
    execFileSync("git", ["add", "tracked.txt"], { cwd: workspace });
    execFileSync("git", ["commit", "-m", "initial"], { cwd: workspace, stdio: "ignore" });
    fs.writeFileSync(path.join(workspace, "tracked.txt"), "two\n", "utf8");
    return workspace;
  } catch {
    return null;
  }
}

test("filesystem.read reads workspace file with structured content", async () => {
  const workspace = makeWorkspace();
  fs.writeFileSync(path.join(workspace, "README.md"), "Odessy read test\n", "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await readFileTool(guard, { path: "README.md" });

  assert.equal(result.status, "ok");
  assert.equal(result.path, "README.md");
  assert.equal(result.content, "Odessy read test\n");
  assert.equal(result.truncated, false);
});

test("filesystem.read denies workspace escape", async () => {
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  await assert.rejects(
    () => readFileTool(guard, { path: "../secret.txt" }),
    /outside the workspace/,
  );
});

test("filesystem.list lists directory entries", async () => {
  const workspace = makeWorkspace();
  fs.mkdirSync(path.join(workspace, "src"));
  fs.writeFileSync(path.join(workspace, "src", "main.py"), "print('hi')\n", "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await listDirectoryTool(guard, { path: "." });

  assert.equal(result.status, "ok");
  assert.equal(result.path, ".");
  assert.deepEqual(result.entries.map((entry) => entry.name), ["src"]);
});

test("project.find finds file names", async () => {
  const workspace = makeWorkspace();
  fs.mkdirSync(path.join(workspace, "src"));
  fs.writeFileSync(path.join(workspace, "src", "policy.py"), "", "utf8");
  fs.writeFileSync(path.join(workspace, "src", "agent.py"), "", "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await findProjectTool(guard, { pattern: "policy", path: "." });

  assert.equal(result.status, "ok");
  assert.deepEqual(result.matches, ["src/policy.py"]);
});

test("project.grep finds content matches", async () => {
  const workspace = makeWorkspace();
  fs.mkdirSync(path.join(workspace, "src"));
  fs.writeFileSync(path.join(workspace, "src", "policy.py"), "ALLOW\nDENY\n", "utf8");
  const guard = createWorkspaceGuard(workspace);

  const result = await grepProjectTool(guard, {
    pattern: "allow",
    path: ".",
    ignore_case: true,
    literal: true,
  });

  assert.equal(result.status, "ok");
  assert.equal(result.matches.length, 1);
  assert.equal(result.matches[0]?.path, "src/policy.py");
  assert.equal(result.matches[0]?.line, 1);
});

test("git read-only tools use fixed argv behavior", async (t) => {
  const workspace = makeGitWorkspace();
  if (workspace === null) {
    t.skip("Node child_process cannot run git in a temp workspace in this environment");
    return;
  }

  const guard = createWorkspaceGuard(workspace);

  const status = await gitStatus(guard);
  const diff = await gitDiff(guard, { mode: "working" });
  const changed = await gitChangedFiles(guard, { mode: "working" });
  const log = await gitLog(guard, { limit: 500 });

  if (
    status.status === "failed" &&
    diff.status === "failed" &&
    changed.status === "failed" &&
    log.status === "failed" &&
    [status.error, diff.error, changed.error, log.error].some((error) =>
      error?.includes("EPERM"),
    )
  ) {
    t.skip("Node child_process cannot execute git tool handlers in this environment");
    return;
  }

  assert.equal(status.status, "ok");
  const statusRaw = status.raw;
  assert.ok(typeof statusRaw === "string");
  assert.match(statusRaw, /tracked\.txt/);
  assert.equal(diff.status, "ok");
  const diffText = diff.diff;
  assert.ok(typeof diffText === "string");
  assert.match(diffText, /two/);
  assert.deepEqual(changed.files, ["tracked.txt"]);
  assert.equal(log.commits.length, 1);
  assert.equal(log.limit, 50);
});

test("laptop.terminal.run executes a command in the workspace", async () => {
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  const result = await laptopTerminalRun(guard, { command: "pwd" });

  assert.equal(result.status, "ok");
  assert.equal(result.cwd, path.resolve(workspace));
  assert.equal(result.command, "pwd");
  assert.equal(result.exit_code, 0);
  assert.match(result.stdout, /.+/);
});
