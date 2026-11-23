/**
 * Live bwrap checks for the Node executor: the boundary really holds on Linux.
 *
 * Skipped unless bwrap is usable. CI's `sandbox-live` job sets `ODESSY_SANDBOX_LIVE=1`,
 * which turns the skip into a failure. Run it on the Jetson before a release:
 * `ODESSY_SANDBOX_LIVE=1 node --test dist/tests/sandbox-live.test.js`.
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { BwrapProvider } from "../src/execution/providers/bwrap.js";
import { ProviderRegistry } from "../src/execution/registry.js";
import { confineArgv, confinementFromRegistry, type ConfinementShape } from "../src/execution/seam.js";
import type { Mode } from "../src/execution/contract.js";

const LIVE = process.env.ODESSY_SANDBOX_LIVE === "1";
const provider = new BwrapProvider();
const evaluation =
  process.platform === "linux"
    ? provider.evaluate({
        policy: { mode: "read-only", workspaceRoot: tmpdir(), deviceGrants: [] },
        isolation: "required",
        capabilities: ["process-exec"],
      })
    : undefined;
const usable = evaluation?.satisfied === true;

if (LIVE && !usable) {
  test("bwrap is usable when ODESSY_SANDBOX_LIVE=1", () => {
    assert.fail(`bwrap is not usable: ${evaluation?.reason ?? "not Linux"}`);
  });
}

function run(workspace: string, command: string, mode: Mode = "read-only", shape: ConfinementShape = {}) {
  const confinement = confinementFromRegistry(mode, "required", workspace, new ProviderRegistry([provider]), shape);
  const confined = confineArgv(confinement, ["/bin/sh", "-c", command], workspace);
  const [executable, ...args] = confined.argv;
  const result = spawnSync(executable!, args, { cwd: workspace, encoding: "utf8", timeout: 30_000 });
  return { status: result.status, stdout: result.stdout ?? "", sandbox: confined.sandbox };
}

function folders(): { workspace: string; outside: string; cleanup: () => void } {
  const workspace = mkdtempSync(join(tmpdir(), "odessy-live-"));
  const outside = join(homedir(), `.odessy-live-node-${process.pid}-${Date.now()}`);
  mkdirSync(outside);
  return {
    workspace,
    outside,
    cleanup: () => {
      rmSync(workspace, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    },
  };
}

const CONNECT = `${process.execPath} -e "require('net').connect(53,'1.1.1.1').on('connect',()=>process.exit(0)).on('error',()=>process.exit(3))"`;

test("read-only cannot write the workspace", { skip: !usable }, () => {
  const { workspace, cleanup } = folders();
  try {
    const result = run(workspace, "touch created");
    assert.notEqual(result.status, 0);
    assert.equal(existsSync(join(workspace, "created")), false);
    assert.equal(result.sandbox.enforcement, "full");
  } finally {
    cleanup();
  }
});

test("workspace-write cannot write outside the workspace", { skip: !usable }, () => {
  const { workspace, outside, cleanup } = folders();
  try {
    assert.equal(run(workspace, "echo ok > created", "workspace-write").status, 0);
    const escaped = run(workspace, `echo no > ${outside}/escaped`, "workspace-write");
    assert.notEqual(escaped.status, 0);
    assert.equal(existsSync(join(outside, "escaped")), false);
  } finally {
    cleanup();
  }
});

test("the network is cut when denied and open otherwise", { skip: !usable }, () => {
  const { workspace, cleanup } = folders();
  try {
    assert.equal(run(workspace, CONNECT, "read-only", { network: "allow" }).status, 0);
    const cut = run(workspace, CONNECT, "read-only", { network: "deny" });
    assert.notEqual(cut.status, 0);
    assert.equal(cut.sandbox.network, "denied");
  } finally {
    cleanup();
  }
});

test("hidden paths cannot be read", { skip: !usable }, () => {
  const { workspace, outside, cleanup } = folders();
  try {
    const secrets = join(outside, "secrets");
    mkdirSync(secrets);
    writeFileSync(join(secrets, "key"), "top secret\n");
    assert.match(run(workspace, `cat ${secrets}/key`).stdout, /top secret/);
    const hidden = run(workspace, `cat ${secrets}/key`, "read-only", { hidden_paths: [secrets] });
    assert.doesNotMatch(hidden.stdout, /top secret/);
    assert.equal(hidden.sandbox.hidden_paths, 1);
  } finally {
    cleanup();
  }
});
