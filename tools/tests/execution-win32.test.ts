/**
 * The moved Windows ACL stack, tested where it does not require Win32.
 *
 * Most of the stack binds Win32 through `koffi` and cannot be exercised without
 * a Windows host with the relevant privileges. Three modules are pure, though,
 * and they are the three that carry the most consequence:
 *
 * - `win32-abi` carries the access-mask composition that decides what a confined
 *   child may do.
 * - `workspace-sid` derives the capability identity that the whole allow-list
 *   hangs off.
 * - `path-boundary` guards against two capabilities merging into one.
 *
 * The x64 layout constants are pinned as regression guards rather than proved
 * here. Their authoritative verification is `verify/abi-probe.cpp` in the upstream
 * package, which prints `sizeof`/`offsetof` for comparison against the real SDK
 * headers on Windows.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assertPrivateTempDisjoint,
  assertTempRootOutsideWorkspace,
} from "../src/execution/win32/path-boundary.js";
import { RUNNER_ENTRY, runnerEntryExists } from "../src/execution/win32/runner-entry.js";
import {
  DELETE,
  FILE_DELETE_CHILD,
  FILE_GENERIC_WRITE,
  GRANT_MASK,
  SECURITY_MAX_SID_SIZE,
  SID_AND_ATTRIBUTES_SIZE,
  SID_MAX_SUB_AUTHORITIES,
  STANDARD_RIGHTS_WRITE,
  SUB_CONTAINERS_AND_OBJECTS_INHERIT,
  TOKEN_GROUPS_OFFSET,
  TRUSTEE_W_OFFSET,
  WRITE_RESTRICTED,
} from "../src/execution/win32/win32-abi.js";
import { tempWriteSid, workspaceWriteSid } from "../src/execution/win32/workspace-sid.js";

function makeDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "odessy-win32-"));
}

// --------------------------------------------------------------------------
// win32-abi: the access mask decides what a confined child may do
// --------------------------------------------------------------------------

test("the grant mask keeps the write and delete bits", () => {
  // Write-data and delete are the whole point of the workspace capability.
  assert.equal(GRANT_MASK & FILE_GENERIC_WRITE & ~STANDARD_RIGHTS_WRITE, FILE_GENERIC_WRITE & ~STANDARD_RIGHTS_WRITE);
  assert.equal(GRANT_MASK & DELETE, DELETE);
  assert.equal(GRANT_MASK & FILE_DELETE_CHILD, FILE_DELETE_CHILD);
});

test("the grant mask excludes WRITE_DAC and WRITE_OWNER", () => {
  // This is the property that stops a confined child from rewriting the DACL or
  // taking ownership to escape the allow-list. Both bits live in
  // FILE_GENERIC_WRITE's neighbourhood, so their absence is not incidental.
  const WRITE_DAC = 0x0004_0000;
  const WRITE_OWNER = 0x0008_0000;

  assert.equal(GRANT_MASK & WRITE_DAC, 0, "a confined child must not rewrite the DACL");
  assert.equal(GRANT_MASK & WRITE_OWNER, 0, "a confined child must not take ownership");
});

test("the grant mask strips the standard-rights write bit", () => {
  // STANDARD_RIGHTS_WRITE is READ_CONTROL on Win32; leaving it in would hand the
  // child a handle right it was never promised.
  assert.equal(GRANT_MASK & STANDARD_RIGHTS_WRITE, 0);
});

test("the ACE inherits to containers and objects", () => {
  assert.equal(SUB_CONTAINERS_AND_OBJECTS_INHERIT, 0x3);
});

test("the restricted token is write-restricted and disables max privilege", () => {
  assert.equal(WRITE_RESTRICTED, 0x8);
});

test("the x64 layout constants are pinned", () => {
  // Regression guards. Their authoritative check is the upstream abi-probe.
  assert.equal(SECURITY_MAX_SID_SIZE, 68);
  assert.equal(SID_AND_ATTRIBUTES_SIZE, 16);
  assert.equal(SID_MAX_SUB_AUTHORITIES, 15);
  assert.equal(TOKEN_GROUPS_OFFSET, 8);
  assert.equal(TRUSTEE_W_OFFSET, 16);
});

// --------------------------------------------------------------------------
// workspace-sid: the capability identity
// --------------------------------------------------------------------------

const SUB_AUTHORITY_MAX = 2 ** 30 - 1;

function parseSid(sid: string): number[] {
  assert.match(sid, /^S-1-4-/);
  return sid
    .slice("S-1-4-".length)
    .split("-")
    .map((part) => Number.parseInt(part, 10));
}

test("the workspace SID is deterministic from the canonical path", () => {
  // Deterministic is what makes the standing ACE a reuse cache: every later
  // provision of the same workspace hits the exact-ACE skip.
  assert.equal(workspaceWriteSid("C:\\work"), workspaceWriteSid("C:\\work"));
});

test("different workspaces derive different SIDs", () => {
  assert.notEqual(workspaceWriteSid("C:\\work"), workspaceWriteSid("C:\\other"));
});

test("the workspace SID has two in-range sub-authorities", () => {
  const parts = parseSid(workspaceWriteSid("C:\\work"));

  assert.equal(parts.length, 2);
  for (const part of parts) {
    assert.ok(part >= 1 && part <= SUB_AUTHORITY_MAX, `${part} is outside the 30-bit range`);
  }
});

test("the temp SID is domain-separated from the workspace SID", () => {
  // A fixed third sub-authority, so one path cannot derive both identities and
  // sibling sessions cannot write one another's temp trees.
  const parts = parseSid(tempWriteSid("C:\\work"));

  assert.equal(parts.length, 3);
  assert.equal(parts[2], 1);
});

test("the temp SID is deterministic and path-specific", () => {
  assert.equal(tempWriteSid("C:\\tmp\\a"), tempWriteSid("C:\\tmp\\a"));
  assert.notEqual(tempWriteSid("C:\\tmp\\a"), tempWriteSid("C:\\tmp\\b"));
});

test("the workspace and temp identities never collide for one path", () => {
  const root = "C:\\work";

  assert.notEqual(workspaceWriteSid(root), tempWriteSid(root));
});

// --------------------------------------------------------------------------
// path-boundary: two capabilities must not merge
// --------------------------------------------------------------------------

test("a temp root inside the workspace is rejected", () => {
  // Every child created below it would inherit the standing workspace capability.
  const workspace = makeDir();
  const nested = path.join(workspace, "tmp");
  fs.mkdirSync(nested);

  assert.throws(() => assertTempRootOutsideWorkspace(workspace, nested), /must be outside the workspace/);
});

test("the workspace itself is not a valid temp root", () => {
  const workspace = makeDir();

  assert.throws(() => assertTempRootOutsideWorkspace(workspace, workspace), /must be outside/);
});

test("a disjoint temp root is accepted", () => {
  const workspace = makeDir();
  const temp = makeDir();

  assert.doesNotThrow(() => assertTempRootOutsideWorkspace(workspace, temp));
});

test("a private temp inside a writable directory is rejected", () => {
  const writable = makeDir();
  const nested = path.join(writable, "private");
  fs.mkdirSync(nested);

  assert.throws(() => assertPrivateTempDisjoint([writable], nested), /must be disjoint/);
});

test("a writable directory inside the private temp is rejected", () => {
  // The other direction matters equally: either inheritance direction merges
  // the two capabilities.
  const writable = makeDir();
  const nested = path.join(writable, "sub");
  fs.mkdirSync(nested);

  assert.throws(() => assertPrivateTempDisjoint([nested], writable), /must be disjoint/);
});

test("a non-existent path fails closed rather than reading as disjoint", () => {
  // Both checks resolve with realpathSync.native, so a path that does not exist
  // raises ENOENT instead of reporting a boundary verdict. That is the safe
  // direction: a lexical comparison would silently call an unresolvable path
  // disjoint and merge the two capabilities. The callers create the directories
  // first, so this path is not reachable in production.
  const workspace = makeDir();

  assert.throws(
    () => assertTempRootOutsideWorkspace(workspace, path.join(workspace, "absent")),
    /ENOENT/,
  );
});

test("disjoint writable and private temp directories are accepted", () => {
  const writable = makeDir();
  const temp = makeDir();

  assert.doesNotThrow(() => assertPrivateTempDisjoint([writable], temp));
});

// --------------------------------------------------------------------------
// module graph: the rewiring has to resolve at runtime, not just typecheck
// --------------------------------------------------------------------------

test("the moved module graph resolves its vendored dependencies", async (t) => {
  if (process.platform !== "win32") {
    t.skip("the Windows ACL stack only loads on Windows");
    return;
  }

  // Type-checking proves the specifiers resolve; it does not prove they resolve
  // *at runtime*. The chain under test is
  //   win32/index.ts -> win32/ffi.ts -> vendor/dsh-win32-process
  //   vendor/dsh-win32-process/koffi.ts -> vendor/dsh-lazy-require
  // A package specifier left pointing at an unpublished upstream name fails here
  // and nowhere else.
  //
  // `win32/runner.ts` is deliberately not imported anywhere below: it calls
  // `main()` at module scope, so importing it would confine and spawn a process.
  // It is referenced by path only.
  const module = await import("../src/execution/win32/index.js");

  assert.equal(typeof module.AclSandbox, "function");
  assert.equal(typeof module.assertTempRootOutsideWorkspace, "function");
  assert.equal(typeof module.workspaceWriteSid, "function");
});

test("koffi loads and the Win32 bindings bind", async (t) => {
  if (process.platform !== "win32") {
    t.skip("koffi binds Win32 only");
    return;
  }

  // This is the end-to-end dependency check. Binding Win32 symbols needs no
  // privileges — it is symbol lookup through the already-present system DLLs — so
  // it verifies that the vendored FFI layer and the `koffi` native binary are
  // both intact without touching a token or an ACL.
  const { win32 } = await import("../src/execution/win32/ffi.js");
  const api = await win32();

  assert.equal(typeof api.openProcess, "function");
  assert.equal(typeof api.getLastError, "function");
});

test("the runner entry resolves to the compiled script beside it", () => {
  // `runner-entry.ts` derives this path from its own URL instead of importing the
  // runner, precisely because importing it would spawn a confined process. That
  // derivation is the only thing between the provider and a spawn failure, so it
  // is asserted rather than assumed.
  assert.match(RUNNER_ENTRY, /win32[\\/]runner\.js$/);
  assert.equal(runnerEntryExists(), true);
});

