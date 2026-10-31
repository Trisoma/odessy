import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  filesystemWriteTool,
  gitChangedFiles,
  gitDiff,
  gitLog,
  gitStatus,
  laptopTerminalRun,
  logAppendTool,
  readFileTool,
  listDirectoryTool,
  fileNameMatches,
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

test("project.grep does not read what filesystem.read would refuse", async () => {
  const workspace = makeWorkspace();
  fs.writeFileSync(path.join(workspace, "notes.txt"), "needle in a note\n", "utf8");
  fs.writeFileSync(path.join(workspace, ".env"), "PGURL=postgres://admin:needle@db/x\n", "utf8");
  fs.writeFileSync(path.join(workspace, ".env.local"), "needle\n", "utf8");
  fs.writeFileSync(path.join(workspace, "server.pem"), "needle\n", "utf8");
  fs.mkdirSync(path.join(workspace, ".ssh"));
  fs.writeFileSync(path.join(workspace, ".ssh", "id_rsa"), "needle\n", "utf8");
  fs.mkdirSync(path.join(workspace, "keys"));
  fs.writeFileSync(path.join(workspace, "keys", "id_ed25519"), "needle\n", "utf8");

  const result = await grepProjectTool(createWorkspaceGuard(workspace), { pattern: "needle" });

  assert.deepEqual(result.matches.map((match) => match.path), ["notes.txt"]);
});

test("project.grep does not follow a link out of the workspace", async (t) => {
  const workspace = makeWorkspace();
  const outside = path.join(makeWorkspace(), "outside.txt");
  fs.writeFileSync(outside, "needle\n", "utf8");
  try {
    fs.symlinkSync(outside, path.join(workspace, "link.txt"));
  } catch {
    t.skip("creating a symbolic link needs a privilege here");
    return;
  }

  const result = await grepProjectTool(createWorkspaceGuard(workspace), { pattern: "needle" });

  assert.equal(result.count, 0);
});

test("a workspace given by its 8.3 short name still contains itself", async (t) => {
  if (process.platform !== "win32") {
    t.skip("8.3 short names are Windows-only");
    return;
  }
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "odessy-long-workspace-name-"));
  fs.writeFileSync(path.join(workspace, "README.md"), "short name\n", "utf8");
  const parent = path.dirname(workspace);
  const leaf = path.basename(workspace);
  // Only the leaf goes through cmd, so a non-ASCII parent is never re-encoded.
  const shortLeaf = execFileSync("cmd", ["/d", "/c", `for %I in (${leaf}) do @echo %~snxI`], {
    cwd: parent,
    encoding: "utf8",
  }).trim();
  if (shortLeaf.toLowerCase() === leaf.toLowerCase()) {
    t.skip("8.3 short names are disabled on this volume");
    return;
  }

  const guard = createWorkspaceGuard(path.join(parent, shortLeaf));

  assert.equal(await guard.resolveExisting("."), guard.root);
  const result = await readFileTool(guard, { path: "README.md" });
  assert.equal(result.status, "ok");
  assert.equal(result.content, "short name\n");
  // filesystem.write reports its path through the lexical (short-name) route.
  const written = await filesystemWriteTool(guard, { path: "notes.txt", content: "x" });
  assert.equal(written.path, "notes.txt");
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

test("project.find accepts globs, as models write them", async () => {
  const workspace = makeWorkspace();
  fs.mkdirSync(path.join(workspace, "src"));
  fs.writeFileSync(path.join(workspace, "src", "policy.py"), "", "utf8");
  fs.writeFileSync(path.join(workspace, "src", "notes.md"), "", "utf8");
  fs.writeFileSync(path.join(workspace, "Settings.toml"), "", "utf8");
  const guard = createWorkspaceGuard(workspace);

  for (const pattern of ["*.py", "**/*.py", "src/*.py", "*.PY"]) {
    const result = await findProjectTool(guard, { pattern, path: "." });
    assert.deepEqual(result.matches, ["src/policy.py"], pattern);
  }
  const prefix = await findProjectTool(guard, { pattern: "settings*", path: "." });
  assert.deepEqual(prefix.matches, ["Settings.toml"]);
  const single = await findProjectTool(guard, { pattern: "note?.md", path: "." });
  assert.deepEqual(single.matches, ["src/notes.md"]);
});

test("project.find glob characters other than * and ? are literal", () => {
  assert.equal(fileNameMatches("a.py", "*.py"), true);
  assert.equal(fileNameMatches("axpy", "*.py"), false);
  assert.equal(fileNameMatches("[x].py", "[x]*"), true);
  assert.equal(fileNameMatches("x.py", "[x]*"), false);
  assert.equal(fileNameMatches("policy.py", "olic"), true);
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

test("laptop.terminal.run preserves configured PowerShell modules", {
  skip: process.platform !== "win32",
}, async () => {
  const workspace = makeWorkspace();
  const modules = path.join(workspace, "modules");
  const module = path.join(modules, "OdessyProbe");
  fs.mkdirSync(module, { recursive: true });
  fs.writeFileSync(path.join(module, "OdessyProbe.psm1"),
    'function Get-OdessyProbe { "module-path-preserved" }', "utf8");
  const previous = process.env.PSModulePath;
  try {
    process.env.PSModulePath = modules;
    const result = await laptopTerminalRun(createWorkspaceGuard(workspace), {
      command: "Get-OdessyProbe",
    });
    assert.equal(result.status, "ok", JSON.stringify(result));
    assert.match(result.stdout, /module-path-preserved/);
  } finally {
    if (previous === undefined) delete process.env.PSModulePath;
    else process.env.PSModulePath = previous;
  }
});

test("laptop.terminal.run executes a command in the workspace", async () => {
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  // This tests command execution and cwd, not PowerShell's cold module-load time.
  // Keep short deadlines in the dedicated timeout/termination tests instead.
  const result = await laptopTerminalRun(guard, { command: "pwd", timeout: 30 });

  assert.equal(result.status, "ok", JSON.stringify(result));
  assert.equal(result.timed_out, false);
  // The canonical root: on Windows runners the temp path is an 8.3 short name
  // (RUNNER~1) that canonicalization expands.
  assert.equal(result.cwd, guard.root);
  assert.equal(result.command, "pwd");
  assert.equal(result.exit_code, 0);
  assert.match(result.stdout, /.+/);
});

test("filesystem.write writes content inside the workspace", async () => {
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  const result = await filesystemWriteTool(guard, {
    path: path.join("src", "notes.txt"),
    content: "hello odessy\n",
  });

  assert.equal(result.status, "ok");
  assert.equal(result.path, "src/notes.txt");
  assert.equal(
    fs.readFileSync(path.join(workspace, "src", "notes.txt"), "utf8"),
    "hello odessy\n",
  );
});

test("filesystem.write denies workspace escape", async () => {
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  await assert.rejects(
    () => filesystemWriteTool(guard, { path: "../secret.txt", content: "x" }),
    /outside the workspace/,
  );
});

test("log.append writes to the fixed internal log path", async () => {
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  const result = await logAppendTool(guard, { content: "hello log" });

  assert.equal(result.status, "ok");
  assert.equal(result.path, ".odessy/logs/odessy.log");
  const raw = fs.readFileSync(path.join(workspace, ".odessy", "logs", "odessy.log"), "utf8");
  assert.match(raw, /hello log/);
});
