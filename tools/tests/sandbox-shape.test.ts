import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { ExecutionPolicy } from "../src/execution/contract.js";
import { limitArgv, normalizeLimits } from "../src/execution/limits.js";
import { bwrapProfileArgs, landlockProfileArgs, seatbeltProfileArgs } from "../src/execution/profiles.js";
import { extraWritableRoots, hiddenRoots, writableRoots } from "../src/execution/roots.js";
import { confineArgv } from "../src/execution/seam.js";
import { UnconfinedProvider } from "../src/execution/providers/unconfined.js";

function policy(mode: ExecutionPolicy["mode"], extra: Partial<ExecutionPolicy> = {}): ExecutionPolicy {
  return { mode, workspaceRoot: "/work", deviceGrants: [], ...extra };
}

test("bwrap cuts the network, hides paths before the writable binds, and binds extra folders", () => {
  const base = mkdtempSync(join(tmpdir(), "odessy-shape-"));
  const folder = join(base, "secrets");
  const file = join(base, "token");
  mkdirSync(folder);
  writeFileSync(file, "x");
  const args = bwrapProfileArgs(
    policy("workspace-write", { network: "deny", hiddenPaths: [folder, file], extraWritable: ["/cache"] }),
  ).join(" ");
  assert.match(args, /--unshare-net/);
  assert.ok(args.includes(`--tmpfs ${folder}`) && args.includes(`--ro-bind /dev/null ${file}`));
  assert.ok(args.indexOf(folder) < args.indexOf("--bind /work /work"));
  assert.ok(args.includes("--bind /cache /cache"));
  assert.ok(!bwrapProfileArgs(policy("read-only")).includes("--unshare-net"));
  assert.ok(!bwrapProfileArgs(policy("read-only", { extraWritable: ["/cache"] })).includes("/cache"));
});

test("seatbelt denies the network and hidden reads; landlock grants extra folders", () => {
  const profile = seatbeltProfileArgs(policy("read-only", { network: "deny", hiddenPaths: ["/secret"] }))[1]!;
  assert.match(profile, /\(deny network\*\)/);
  assert.match(profile, /\(deny file-read\* \(literal "\/secret"\)\)/);
  assert.deepEqual(landlockProfileArgs(policy("workspace-write", { extraWritable: ["/cache"] })).slice(-2), [
    "--rw",
    "/cache",
  ]);
});

test("writable roots include extra folders only when writing", () => {
  const extra = mkdtempSync(join(tmpdir(), "odessy-extra-"));
  assert.ok(writableRoots(policy("workspace-write", { extraWritable: [extra] })).length >= 3);
  assert.deepEqual(writableRoots(policy("read-only", { extraWritable: [extra] })), []);
});

test("hidden roots drop missing paths and anything overlapping the workspace", () => {
  const base = mkdtempSync(join(tmpdir(), "odessy-hidden-"));
  const secrets = join(base, "secrets");
  const workspace = join(base, "work");
  mkdirSync(secrets);
  mkdirSync(join(workspace, "inner"), { recursive: true });
  const roots = hiddenRoots([secrets, join(base, "missing"), base, join(workspace, "inner"), "relative"], workspace);
  assert.equal(roots.length, 1);
  assert.ok(roots[0]!.endsWith("secrets"));
});

test("extra writable roots refuse filesystem roots and missing folders", () => {
  const cache = mkdtempSync(join(tmpdir(), "odessy-cache-"));
  const root = process.platform === "win32" ? "C:\\" : "/";
  assert.equal(extraWritableRoots([cache, root, join(cache, "missing")]).length, 1);
});

test("an unconfined run never claims a cut network", () => {
  const confined = confineArgv(
    { provider: new UnconfinedProvider(), policy: policy("read-only", { network: "deny" }) },
    ["echo"],
    "/work",
  );
  assert.equal(confined.sandbox.network, "open");
  assert.equal(confined.sandbox.hidden_paths, 0);
  assert.equal(confineArgv(undefined, ["echo"], "/work").sandbox.network, undefined);
});

test("limits are clamped, and reported as not applied where prlimit cannot run", () => {
  assert.deepEqual(normalizeLimits({ memory_mb: 10 ** 9, processes: 0, cpu_seconds: 1.5 }), { memory_mb: 1 << 20 });
  assert.equal(normalizeLimits({}), undefined);
  const { argv, report } = limitArgv({ memory_mb: 64, processes: 4 }, ["sh", "-c", "true"]);
  if (process.platform === "win32") {
    assert.deepEqual(argv, ["sh", "-c", "true"]);
    assert.deepEqual(report.not_applied, ["memory_mb", "processes"]);
  } else if (argv[0] === "prlimit") {
    assert.ok(argv.includes(`--data=${64 * 1024 * 1024}`));
    assert.deepEqual(report.applied, { memory_mb: 64 });
    assert.deepEqual(report.not_applied, ["processes"]);
  }
});
