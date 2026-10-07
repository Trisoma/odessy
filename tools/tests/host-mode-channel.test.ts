/**
 * The host side of the mode channel.
 *
 * The authority layer resolves a mode and sends it with the call. This side answers one
 * question: *can I confine under this, and how completely?* It never decides the mode —
 * doing so would be a second policy authority, and it would drift from the first.
 *
 * The first test here exists because of a real defect. `mode` was injected by the agent
 * but not declared in this schema, and zod **strips** unknown keys silently. So the
 * agent logged a resolved mode, the host discarded it, and the command ran unconfined
 * while the record claimed a boundary that did not exist — the precise failure this
 * architecture exists to prevent, and invisible because nothing errored.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { z } from "zod";

import {
  UnsatisfiableRequirementError,
  type Capability,
  type ConfineResult,
  type Evaluation,
  type ExecutionPolicy,
  type ExecutionProvider,
  type ProbeReport,
  type Requirement,
} from "../src/execution/contract.js";
import { defaultExecutionProviders } from "../src/execution/providers/index.js";
import { UnconfinedProvider } from "../src/execution/providers/unconfined.js";
import { ProviderRegistry, withUnconfinedFallback } from "../src/execution/registry.js";
import { confinementFromRegistry, resolveHostConfinement } from "../src/execution/seam.js";
import { laptopTerminalRunInputSchema } from "../src/tools/index.js";

const schema = z.object(laptopTerminalRunInputSchema);

/** A backend that confines every mode except the one that provides no confinement. */
class FakeConfiningProvider implements ExecutionProvider {
  readonly providerId = "fake-confining";

  capabilities(): readonly Capability[] {
    return ["process-exec", "fs-read", "fs-write"];
  }

  probe(): ProbeReport {
    return { providerId: this.providerId, available: true, facts: {}, reason: "test" };
  }

  evaluate(requirement: Requirement): Evaluation {
    if (requirement.policy.mode === "danger-full-access") {
      return {
        satisfied: false,
        enforcement: "none",
        limitations: [],
        reason: "this backend confines; it does not do full access",
      };
    }
    return { satisfied: true, enforcement: "full", limitations: [], reason: "confines fully" };
  }

  confine(argv: readonly string[], _policy: ExecutionPolicy): ConfineResult {
    return {
      argv,
      enforcement: "full",
      limitations: [],
      denialSignatures: [],
      runnerFailureRules: [],
      providerId: this.providerId,
    };
  }
}

function testRegistry(): ProviderRegistry {
  return new ProviderRegistry([new FakeConfiningProvider(), new UnconfinedProvider()]);
}

// --------------------------------------------------------------------------
// The defect: a mode must not be silently dropped
// --------------------------------------------------------------------------

test("a mode sent by the authority layer survives schema parsing", () => {
  const parsed = schema.safeParse({ command: "pwd", mode: "read-only" });

  assert.equal(parsed.success, true);
  assert.ok(parsed.success);
  // Asserting the field is PRESENT, not merely that parsing succeeded. `success: true`
  // was exactly what hid this bug: zod stripped the key and reported success anyway.
  assert.equal(parsed.data.mode, "read-only");
});

test("the isolation requirement survives schema parsing too", () => {
  const parsed = schema.safeParse({ command: "pwd", mode: "read-only", isolation: "required" });

  assert.equal(parsed.success, true);
  assert.ok(parsed.success);
  assert.equal(parsed.data.isolation, "required");
});

test("an unknown mode is rejected rather than silently dropped", () => {
  // The vocabulary is closed, so a mode this host does not understand is an error
  // rather than something to quietly discard.
  assert.equal(schema.safeParse({ command: "pwd", mode: "root" }).success, false);
  assert.equal(schema.safeParse({ command: "pwd", isolation: "maybe" }).success, false);
});

test("a call carrying no mode is still accepted and resolves to nothing", () => {
  // A caller that resolved no mode is not an error: it runs unconfined and the result
  // reports that, rather than the host inventing a boundary.
  const parsed = schema.safeParse({ command: "pwd" });

  assert.equal(parsed.success, true);
  assert.ok(parsed.success);
  assert.equal(parsed.data.mode, undefined);
});

// --------------------------------------------------------------------------
// Selecting a provider for the supplied mode
// --------------------------------------------------------------------------

test("a confined mode selects the backend that confines it", () => {
  const confinement = confinementFromRegistry("read-only", "preferred", "/work", testRegistry());

  assert.equal(confinement.provider.providerId, "fake-confining");
  // The mode the peer sent is the mode the provider is asked to confine under.
  assert.equal(confinement.policy.mode, "read-only");
});

test("full access under a softer requirement falls back to the named absence", () => {
  const confinement = confinementFromRegistry(
    "danger-full-access",
    "preferred",
    "/work",
    testRegistry(),
  );

  // `preferred` accepts a weaker boundary, so the explicit unconfined provider is the
  // answer -- and it reports `enforcement: "none"` rather than staying silent.
  assert.equal(confinement.provider.providerId, "unconfined");
});

test("required isolation refuses full access instead of downgrading", () => {
  // The invariant, enforced on the host as well as on the authority side. Two layers
  // disagreeing about whether a boundary exists is the failure to avoid, so the host
  // must not quietly satisfy `required` with no confinement.
  assert.throws(
    () =>
      confinementFromRegistry("danger-full-access", "required", "/work", testRegistry()),
    UnsatisfiableRequirementError,
  );
});

test("required isolation is satisfied by a backend that actually confines", () => {
  const confinement = confinementFromRegistry("workspace-write", "required", "/work", testRegistry());

  assert.equal(confinement.provider.providerId, "fake-confining");
});

// --------------------------------------------------------------------------
// The real host
// --------------------------------------------------------------------------

test("this host resolves a confined mode with a real backend", () => {
  const registry = withUnconfinedFallback(defaultExecutionProviders());

  const { provider } = confinementFromRegistry("read-only", "preferred", process.cwd(), registry);

  // On Windows the ACL runner is the backend; asserting the id keeps this from passing
  // merely because SOME provider answered.
  if (process.platform === "win32") {
    assert.equal(provider.providerId, "windows-acl");
  }
  assert.notEqual(provider.providerId, "unconfined");
});

// --------------------------------------------------------------------------
// The host must not infer the requirement
// --------------------------------------------------------------------------

/** A backend that governs only part of what its modes promise. */
class FakePartialProvider implements ExecutionProvider {
  readonly providerId = "fake-partial";

  capabilities(): readonly Capability[] {
    return ["process-exec", "fs-read", "fs-write"];
  }

  probe(): ProbeReport {
    return { providerId: this.providerId, available: true, facts: {}, reason: "test" };
  }

  evaluate(requirement: Requirement): Evaluation {
    if (requirement.policy.mode === "danger-full-access") {
      return { satisfied: false, enforcement: "none", limitations: [], reason: "no full access" };
    }
    // `partial` is an acceptable answer to a request that merely prefers isolation and
    // an unacceptable one to a request that requires it. The declared limitation is not
    // decoration: the contract refuses `partial` that names no shortfall.
    const satisfied = requirement.isolation !== "required";
    return {
      satisfied,
      enforcement: "partial",
      limitations: ["cannot_restrict_reads"],
      reason: satisfied ? "partial, which preferred accepts" : "required needs an absolute boundary",
    };
  }

  confine(argv: readonly string[], _policy: ExecutionPolicy): ConfineResult {
    return {
      argv,
      enforcement: "partial",
      limitations: ["cannot_restrict_reads"],
      denialSignatures: [],
      runnerFailureRules: [],
      providerId: this.providerId,
    };
  }
}

test("a mode without its requirement is refused, not defaulted", () => {
  // This is the hole. Defaulting `isolation` to `preferred` when the peer omitted it
  // meant a `required` demand could arrive as a `preferred` one -- the host silently
  // weakening the authority layer's decision. Inferring is the bug; refusing is the fix.
  assert.throws(
    () => resolveHostConfinement({ mode: "read-only" }, "/work", testRegistry()),
    /mode and isolation together/,
  );
});

test("a requirement without a mode is refused too", () => {
  // The pairing runs both ways: a requirement with no mode describes nothing.
  assert.throws(
    () => resolveHostConfinement({ isolation: "required" }, "/work", testRegistry()),
    /mode and isolation together/,
  );
});

test("expressing no requirement at all is not an error", () => {
  // Distinct from guessing: nothing was asked for, so nothing is assumed, and the run
  // reports `enforcement: "none"` rather than implying a boundary.
  assert.equal(resolveHostConfinement({}, "/work", testRegistry()), undefined);
});

test("the same mode under a stricter requirement resolves differently", () => {
  // The decisive pair. If this host were inferring `preferred` -- as it did before the
  // fix -- both halves would select the partial backend and the second would pass.
  const registry = new ProviderRegistry([new FakePartialProvider(), new UnconfinedProvider()]);

  const preferred = resolveHostConfinement(
    { mode: "read-only", isolation: "preferred" },
    "/work",
    registry,
  );
  assert.equal(preferred?.provider.providerId, "fake-partial");

  assert.throws(
    () =>
      resolveHostConfinement({ mode: "read-only", isolation: "required" }, "/work", registry),
    UnsatisfiableRequirementError,
  );
});
