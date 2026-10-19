/**
 * The executor seam, asserted rather than assumed.
 *
 * Mirrors `tests/test_execution_wiring.py`. Both languages must reach the same
 * decision from the same inputs, so the same properties are pinned on each side.
 *
 * The property that matters most is that the argv which actually runs is the one
 * `confine()` returned. A seam that confined for show and then spawned the
 * original command would satisfy every test that only inspects the report.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import type {
  Capability,
  ConfineResult,
  Evaluation,
  ExecutionPolicy,
  ExecutionProvider,
  ProbeReport,
  Requirement,
} from "../src/execution/contract.js";
import { confineArgv } from "../src/execution/seam.js";
import { laptopTerminalRun } from "../src/tools/index.js";
import { createWorkspaceGuard } from "../src/workspace/path-guard.js";

/** A backend that replaces the argv, so a test can see that it took effect. */
class RewritingProvider implements ExecutionProvider {
  readonly providerId = "test-rewriting";

  constructor(
    private readonly script = "console.log('from-the-confinement')",
    private readonly enforcement: "full" | "partial" | "none" = "full",
    private readonly limitations: readonly string[] = [],
  ) {}

  capabilities(): readonly Capability[] {
    return ["process-exec"];
  }

  probe(): ProbeReport {
    return { providerId: this.providerId, available: true, facts: {}, reason: "test" };
  }

  evaluate(_requirement: Requirement): Evaluation {
    return {
      satisfied: true,
      enforcement: this.enforcement,
      limitations: [...this.limitations] as Evaluation["limitations"],
      reason: "test",
    };
  }

  confine(_argv: readonly string[], _policy: ExecutionPolicy): ConfineResult {
    return {
      argv: [process.execPath, "-e", this.script],
      enforcement: this.enforcement,
      limitations: [...this.limitations] as ConfineResult["limitations"],
      denialSignatures: [],
      runnerFailureRules: [],
      providerId: this.providerId,
    };
  }
}

class UnavailableProvider implements ExecutionProvider {
  readonly providerId = "test-unavailable";

  capabilities(): readonly Capability[] {
    return ["process-exec"];
  }

  probe(): ProbeReport {
    return { providerId: this.providerId, available: false, facts: {}, reason: "test" };
  }

  evaluate(_requirement: Requirement): Evaluation {
    return { satisfied: false, enforcement: "none", limitations: [], reason: "unavailable" };
  }

  confine(_argv: readonly string[], _policy: ExecutionPolicy): ConfineResult {
    throw new Error("this host cannot confine");
  }
}

class MissingExecutableProvider implements ExecutionProvider {
  readonly providerId = "test-missing-executable";

  capabilities(): readonly Capability[] {
    return ["process-exec"];
  }

  probe(): ProbeReport {
    return { providerId: this.providerId, available: true, facts: {}, reason: "test" };
  }

  evaluate(_requirement: Requirement): Evaluation {
    return { satisfied: true, enforcement: "full", limitations: [], reason: "test" };
  }

  confine(_argv: readonly string[], _policy: ExecutionPolicy): ConfineResult {
    return {
      argv: ["odessy-definitely-not-a-real-executable-xyz"],
      enforcement: "full",
      limitations: [],
      denialSignatures: [],
      runnerFailureRules: [],
      providerId: this.providerId,
    };
  }
}

function makeWorkspace(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "odessy-seam-"));
}

function policy(workspaceRoot: string): ExecutionPolicy {
  return { mode: "workspace-write", workspaceRoot, deviceGrants: [] };
}

// --------------------------------------------------------------------------
// The argv that runs is the one confine() returned
// --------------------------------------------------------------------------

test("the confined argv is what runs", async () => {
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  const result = await laptopTerminalRun(guard, { command: "echo original" }, {
    provider: new RewritingProvider(),
    policy: policy(workspace),
  });

  assert.equal(result.exit_code, 0);
  assert.match(result.stdout, /from-the-confinement/);
  // The shell command the tool built must NOT have run.
  assert.doesNotMatch(result.stdout, /original/);
});

test("no backend is reported rather than implied", () => {
  // The absence is loud: a caller never has to infer enforcement from silence.
  const { argv, sandbox } = confineArgv(undefined, ["echo", "hi"], "/work");

  assert.deepEqual(argv, ["echo", "hi"]);
  assert.equal(sandbox.provider_id, "unconfined");
  assert.equal(sandbox.enforcement, "none");
  assert.deepEqual(sandbox.limitations, []);
});

test("the report names the backend and its completeness", () => {
  const { sandbox } = confineArgv(
    {
      provider: new RewritingProvider("x", "partial", ["cannot_restrict_reads"]),
      policy: policy("/work"),
    },
    ["echo"],
    "/work",
  );

  assert.equal(sandbox.provider_id, "test-rewriting");
  assert.equal(sandbox.enforcement, "partial");
  assert.deepEqual(sandbox.limitations, ["cannot_restrict_reads"]);
});

test("a timeout still reports the confinement", async () => {
  // Both return paths carry the report: a caller reading a timed-out result needs
  // to know what confined it just as much as one reading a success.
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  const result = await laptopTerminalRun(guard, { command: "echo hi", timeout: 1 }, {
    provider: new RewritingProvider("setTimeout(function(){}, 30000)"),
    policy: policy(workspace),
  });

  assert.equal(result.timed_out, true);
  assert.equal(result.status, "timeout");
  // No process exit status exists: the executor terminated the process, so the code
  // a killed process happens to report is a kill artifact, not the command's outcome.
  assert.equal(result.exit_code, null);
  assert.equal(result.sandbox.provider_id, "test-rewriting");
});

test("an unavailable backend stops the command", async () => {
  // It must throw, never fall back to running unconfined: a caller that asked for
  // a boundary would otherwise get a record indistinguishable from one where the
  // boundary held.
  const workspace = makeWorkspace();
  const guard = createWorkspaceGuard(workspace);

  await assert.rejects(
    laptopTerminalRun(guard, { command: "echo hi" }, {
      provider: new UnavailableProvider(),
      policy: policy(workspace),
    }),
    /cannot confine/,
  );
});

// --------------------------------------------------------------------------
// What `exit_code` means
// --------------------------------------------------------------------------
//
// The contract distinguishes a *process outcome* from *executor termination*:
//
//   command exits 0     -> exit_code 0,    timed_out false, status "ok"
//   command exits 7     -> exit_code 7,    timed_out false, status "failed"
//   executor timeout    -> exit_code null, timed_out true,  status "timeout"
//   spawn failure       -> rejects; there is no result to describe
//
// `null` rather than a sentinel because a sentinel is indistinguishable from a real
// exit code, and so reads as the command's own outcome. Python pins the identical
// matrix; the two must not drift, because everything above this layer reads the same
// field.

test("a successful command reports its exit code", async () => {
  const workspace = makeWorkspace();
  // Check the exit-code contract without racing cold Windows shell startup.
  const result = await laptopTerminalRun(createWorkspaceGuard(workspace), {
    command: "exit 0",
    timeout: 30,
  });

  assert.equal(result.exit_code, 0, JSON.stringify(result));
  assert.equal(result.timed_out, false);
  assert.equal(result.status, "ok");
});

test("a failing command reports its real code", async () => {
  // A real non-zero status is preserved as-is: this is the case a sentinel would
  // collide with if the sentinel were a plausible exit code.
  const workspace = makeWorkspace();
  const result = await laptopTerminalRun(createWorkspaceGuard(workspace), {
    command: "exit 7",
    timeout: 30,
  });

  assert.equal(result.exit_code, 7, JSON.stringify(result));
  assert.equal(result.timed_out, false);
  assert.equal(result.status, "failed");
});

test("a spawn failure produces no result at all", async () => {
  // It is not a result carrying a null exit code: the command never ran, so there is
  // no outcome to describe. Rejecting keeps "the command failed" and "the command
  // never started" distinct, which a synthetic result would blur. The assertion is
  // on the rejection rather than the message, which is platform-specific.
  const workspace = makeWorkspace();

  await assert.rejects(
    laptopTerminalRun(createWorkspaceGuard(workspace), { command: "echo hi" }, {
      provider: new MissingExecutableProvider(),
      policy: policy(workspace),
    }),
    (error: unknown) => error instanceof Error,
  );
});

test("a timeout means the executor terminated the process", async () => {
  // A REAL long-running process, on the REAL timeout path. The rewriting provider
  // changes which command runs; it does not stub the timeout mechanism, so the timer
  // genuinely fires and the termination path genuinely runs.
  const workspace = makeWorkspace();
  const marker = path.join(workspace, "survived.txt");
  const script =
    `const fs = require("fs");` +
    `setTimeout(() => fs.writeFileSync(${JSON.stringify(marker)}, "survived"), 2000);`;

  const started = Date.now();
  const result = await laptopTerminalRun(
    createWorkspaceGuard(workspace),
    { command: "echo hi", timeout: 1 },
    { provider: new RewritingProvider(script), policy: policy(workspace) },
  );
  const elapsed = Date.now() - started;

  // Three separate claims, so a regression in one names itself rather than hiding
  // behind the others.
  assert.equal(result.exit_code, null);
  assert.equal(result.timed_out, true);
  assert.equal(result.status, "timeout");

  // Returning well before the child's own delay elapsed is something waiting the
  // process out could not have achieved.
  assert.ok(elapsed < 2500, `returned after ${elapsed}ms, which suggests no termination`);

  // The load-bearing assertion. If termination silently failed, the child would exit
  // on its own and every assertion above would still hold — the flags would be set
  // and the elapsed bound satisfied. This marker is written only by a process that
  // survived to run its callback.
  await new Promise((resolve) => setTimeout(resolve, 2000));
  assert.equal(fs.existsSync(marker), false, "the confined process survived its own termination");
});

test("the termination marker can appear at all", async () => {
  // The control for the test above. Without it, "the marker is absent" could mean the
  // process was terminated OR that the script never ran. Here the same script completes,
  // so the absence above is attributable to termination and nothing else.
  const workspace = makeWorkspace();
  const marker = path.join(workspace, "survived.txt");
  const script =
    `const fs = require("fs");` +
    `setTimeout(() => fs.writeFileSync(${JSON.stringify(marker)}, "survived"), 1000);`;

  const result = await laptopTerminalRun(
    createWorkspaceGuard(workspace),
    { command: "echo hi", timeout: 10 },
    { provider: new RewritingProvider(script), policy: policy(workspace) },
  );

  assert.equal(result.timed_out, false);
  assert.equal(result.exit_code, 0);
  assert.equal(fs.existsSync(marker), true, "the marker mechanism itself is broken");
});
