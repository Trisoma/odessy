import assert from "node:assert/strict";
import test from "node:test";

import type { ExecutionPolicy } from "../src/execution/contract.js";
import {
  bwrapCommand,
  bwrapProfileArgs,
  landlockCommand,
  landlockProfileArgs,
  seatbeltCommand,
  seatbeltProfileArgs,
} from "../src/execution/profiles.js";

function policy(mode: ExecutionPolicy["mode"], workspaceRoot = "/work"): ExecutionPolicy {
  return { mode, workspaceRoot, deviceGrants: [] };
}

test("every profile refuses a mode that requests no confinement", () => {
  for (const build of [bwrapProfileArgs, landlockProfileArgs, seatbeltProfileArgs]) {
    assert.throws(() => build(policy("danger-full-access")), /requests no confinement/);
  }
});

test("bwrap read-only binds the host root read-only", () => {
  const args = bwrapProfileArgs(policy("read-only"));

  assert.deepEqual(args.slice(0, 4), ["--ro-bind", "/", "/", "--dev"]);
  assert.ok(args.includes("--die-with-parent"));
});

test("bwrap read-only grants no writable workspace", () => {
  const args = bwrapProfileArgs(policy("read-only"));

  assert.ok(!args.includes("--bind"));
  assert.ok(!args.includes("--tmpfs"));
  assert.ok(!args.includes("/work"));
});

test("bwrap read-only uses a fresh dev and a private pid namespace", () => {
  // These two are why bwrap restricts more than the file-effect vocabulary: a
  // fresh /dev shadows host device nodes, and --unshare-pid hides processes.
  const args = bwrapProfileArgs(policy("read-only"));

  assert.equal(args[args.indexOf("--dev") + 1], "/dev");
  assert.ok(args.includes("--unshare-pid"));
  assert.equal(args[args.indexOf("--proc") + 1], "/proc");
});

test("bwrap workspace-write adds an ephemeral tmp and the workspace", () => {
  const args = bwrapProfileArgs(policy("workspace-write", "/work"));

  assert.equal(args[args.indexOf("--tmpfs") + 1], "/tmp");
  const bind = args.indexOf("--bind");
  assert.deepEqual(args.slice(bind + 1, bind + 3), ["/work", "/work"]);
});

test("bwrap command places the separator before the caller command", () => {
  const argv = bwrapCommand("/usr/bin/bwrap", ["echo", "hi"], policy("read-only"));

  assert.equal(argv[0], "/usr/bin/bwrap");
  assert.deepEqual(argv.slice(argv.indexOf("--") + 1), ["echo", "hi"]);
});

test("landlock read-only grants the root and only the null sink", () => {
  assert.deepEqual(landlockProfileArgs(policy("read-only")), [
    "--ro",
    "/",
    "--rw",
    "/dev/null",
  ]);
});

test("landlock workspace-write grants tmp and the workspace", () => {
  assert.deepEqual(landlockProfileArgs(policy("workspace-write", "/work")), [
    "--ro",
    "/",
    "--rw",
    "/dev/null",
    "--rw",
    "/tmp",
    "--rw",
    "/work",
  ]);
});

test("landlock never grants a read-write root", () => {
  // Landlock rulesets are allow-lists; a stray `--rw /` lifts the confinement.
  for (const mode of ["read-only", "workspace-write"] as const) {
    const args = landlockProfileArgs(policy(mode));
    args.forEach((token, index) => {
      if (token === "--rw") {
        assert.notEqual(args[index + 1], "/");
      }
    });
  }
});

test("landlock command places the separator before the caller command", () => {
  const argv = landlockCommand("/usr/bin/landlock-run", ["echo", "hi"], policy("read-only"));

  assert.equal(argv[0], "/usr/bin/landlock-run");
  assert.deepEqual(argv.slice(argv.indexOf("--") + 1), ["echo", "hi"]);
});

test("seatbelt read-only denies writes and allows only the sink", () => {
  const args = seatbeltProfileArgs(policy("read-only"));
  const profile = args[args.indexOf("-p") + 1] as string;

  assert.equal(args[0], "-p");
  assert.ok(profile.includes("(deny file-write*)"));
  assert.ok(profile.includes('(allow file-write* (literal "/dev/null"))'));
  assert.ok(!profile.includes("subpath"), "read-only must not allow a writable root");
});

test("seatbelt workspace-write allows the shared writable roots", () => {
  const args = seatbeltProfileArgs(policy("workspace-write", "/work"));
  const profile = args[args.indexOf("-p") + 1] as string;

  assert.ok(profile.includes("subpath"));
  assert.ok(profile.includes('"/work"'));
});

test("seatbelt escapes backslashes before quotes", () => {
  // Escaping the quote first would corrupt a path containing a backslash.
  const args = seatbeltProfileArgs({
    mode: "workspace-write",
    workspaceRoot: '/we"ird\\path',
    deviceGrants: [],
  });
  const profile = args[args.indexOf("-p") + 1] as string;

  assert.ok(profile.includes('\\"'), "the embedded quote must be escaped");
  assert.ok(profile.includes("\\\\"), "the embedded backslash must be escaped");
});

test("seatbelt command places the separator before the caller command", () => {
  const argv = seatbeltCommand("/usr/bin/sandbox-exec", ["echo", "hi"], policy("read-only"));

  assert.equal(argv[0], "/usr/bin/sandbox-exec");
  assert.deepEqual(argv.slice(argv.indexOf("--") + 1), ["echo", "hi"]);
});
