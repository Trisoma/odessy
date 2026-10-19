import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPABILITIES,
  CONFINED_MODES,
  CONTRACT,
  CONTRACT_VERSION,
  DEVICE_CAPABILITIES,
  ENFORCEMENT_LEVELS,
  ESCALATION_OUTCOMES,
  ESCALATION_SUBJECTS,
  ESCALATION_TARGETS,
  ISOLATION_REQUIREMENTS,
  LIMITATIONS,
  MODES,
  PROVIDER_UNAVAILABLE,
  ProviderContractError,
  ProviderUnavailableError,
  REQUIREMENT_UNSATISFIED,
  UnsatisfiableRequirementError,
  WIDER_MODES,
  escalationHint,
  sandboxDenialMarker,
  validateConfineResult,
  validateEvaluation,
  type CapabilityProvider,
  type ConfineResult,
  type Evaluation,
  type ExecutionProvider,
  type ExecutionPolicy,
  type ManagedDeviceProvider,
  type Mode,
  type Requirement,
} from "../src/execution/contract.js";

/**
 * Guard against `contract.json` growing into a specification file. It is the
 * cross-language vocabulary and protocol strings only; behaviour belongs in code
 * and tests.
 */
const EXPECTED_TOP_LEVEL_KEYS = [
  "$comment",
  "version",
  "modes",
  "confined_modes",
  "wider_modes",
  "escalation_targets",
  "escalation_outcomes",
  "escalation_subjects",
  "enforcement_levels",
  "isolation_requirements",
  "capabilities",
  "device_capabilities",
  "limitations",
  "markers",
].sort();

const READ_ONLY_POLICY: ExecutionPolicy = {
  mode: "read-only",
  workspaceRoot: "/tmp",
  deviceGrants: [],
};

test("contract.json stays a vocabulary file", () => {
  assert.deepEqual(Object.keys(CONTRACT).sort(), EXPECTED_TOP_LEVEL_KEYS);
  assert.equal(CONTRACT.version, CONTRACT_VERSION);
});

test("vocabulary matches contract.json", () => {
  assert.deepEqual(CONTRACT.modes, [...MODES]);
  assert.deepEqual(CONTRACT.confined_modes, [...CONFINED_MODES]);
  assert.deepEqual(CONTRACT.escalation_targets, [...ESCALATION_TARGETS]);
  assert.deepEqual(CONTRACT.escalation_outcomes, [...ESCALATION_OUTCOMES]);
  assert.deepEqual(CONTRACT.escalation_subjects, [...ESCALATION_SUBJECTS]);
  assert.deepEqual(CONTRACT.enforcement_levels, [...ENFORCEMENT_LEVELS]);
  assert.deepEqual(CONTRACT.isolation_requirements, [...ISOLATION_REQUIREMENTS]);
  assert.deepEqual(CONTRACT.capabilities, [...CAPABILITIES]);
  assert.deepEqual(CONTRACT.device_capabilities, [...DEVICE_CAPABILITIES]);
  assert.deepEqual(CONTRACT.limitations, [...LIMITATIONS]);
  assert.deepEqual(CONTRACT.wider_modes, {
    "read-only": [...WIDER_MODES["read-only"]],
    "workspace-write": [...WIDER_MODES["workspace-write"]],
  });
});

test("the escalation ladder is strictly wider and closed", () => {
  // Widened views: the const tuples narrow includes()/indexOf() to their own literals.
  const allModes: readonly Mode[] = MODES;
  const targets: readonly Mode[] = ESCALATION_TARGETS;

  assert.ok(!targets.includes("read-only"));
  for (const source of CONFINED_MODES) {
    for (const target of WIDER_MODES[source]) {
      assert.ok(targets.includes(target), `${target} is not an escalation target`);
      assert.notEqual(target, source, "an escalation target may not equal its source");
      assert.ok(
        allModes.indexOf(target) > allModes.indexOf(source),
        `${target} is not strictly wider than ${source}`,
      );
    }
  }
});

test("protocol strings are byte exact", () => {
  assert.equal(
    sandboxDenialMarker("read-only"),
    "[sandbox: file access denied under read-only mode]",
  );
  assert.equal(
    sandboxDenialMarker("workspace-write"),
    "[sandbox: file access denied under workspace-write mode]",
  );
  assert.equal(
    escalationHint("command"),
    "[sandbox: escalation available — retry this exact command once with " +
      "sandbox_permissions (the narrowest wider mode that suffices) + justification; " +
      "the approval prompt asks the user]",
  );
  assert.ok(escalationHint("operation").includes("this exact operation once"));
});

test("partial without a declared limitation is a contract violation", () => {
  const evaluation: Evaluation = {
    satisfied: false,
    enforcement: "partial",
    limitations: [],
    reason: "",
  };
  assert.throws(() => validateEvaluation(evaluation, "demo"), ProviderContractError);
  assert.throws(() => validateEvaluation(evaluation, "demo"), /no declared limitation/);
});

test("partial with a named limitation is valid", () => {
  const evaluation: Evaluation = {
    satisfied: false,
    enforcement: "partial",
    limitations: ["cannot_deny_truncate"],
    reason: "older ABI",
  };
  assert.doesNotThrow(() => validateEvaluation(evaluation, "demo"));
});

test("full may carry scope limitations", () => {
  // host_kernel_shared is a property of same-world backends, not a defect.
  const evaluation: Evaluation = {
    satisfied: true,
    enforcement: "full",
    limitations: ["host_kernel_shared"],
    reason: "",
  };
  assert.doesNotThrow(() => validateEvaluation(evaluation, "demo"));
});

test("none needs no declared limitation", () => {
  const evaluation: Evaluation = {
    satisfied: false,
    enforcement: "none",
    limitations: [],
    reason: "no backend",
  };
  assert.doesNotThrow(() => validateEvaluation(evaluation, "demo"));
});

test("confine results enforce the partial invariant too", () => {
  const result: ConfineResult = {
    argv: ["true"],
    enforcement: "partial",
    limitations: [],
    denialSignatures: [],
    runnerFailureRules: [],
    providerId: "demo",
  };
  assert.throws(() => validateConfineResult(result), /no declared limitation/);
});

test("error codes are distinguishable", () => {
  assert.equal(new ProviderUnavailableError("read-only").code, PROVIDER_UNAVAILABLE);
  const requirement: Requirement = {
    policy: READ_ONLY_POLICY,
    isolation: "required",
    capabilities: ["process-exec"],
  };
  assert.equal(new UnsatisfiableRequirementError(requirement, []).code, REQUIREMENT_UNSATISFIED);
  assert.equal(new ProviderContractError("x").code, "PROVIDER_CONTRACT_VIOLATION");
});

test("unavailable says it will not run unconfined", () => {
  const error = new ProviderUnavailableError("workspace-write", "bwrap: missing");
  assert.match(error.message, /refusing to run the command unconfined/);
  assert.match(error.message, /bwrap: missing/);
  assert.equal(error.mode, "workspace-write");
});

test("unsatisfiable reports every candidate", () => {
  const requirement: Requirement = {
    policy: READ_ONLY_POLICY,
    isolation: "required",
    capabilities: ["process-exec"],
  };
  const evaluations: [string, Evaluation][] = [
    ["bwrap", { satisfied: false, enforcement: "none", limitations: [], reason: "not installed" }],
    ["landlock", { satisfied: false, enforcement: "none", limitations: [], reason: "not enabled" }],
  ];
  const error = new UnsatisfiableRequirementError(requirement, evaluations);
  assert.match(error.message, /not installed/);
  assert.match(error.message, /not enabled/);
  assert.equal(error.evaluations.length, 2);
});

test("a managed device is not an execution provider", () => {
  // A device provider declares device capabilities and offers no confine/execute.
  const deviceOnly = {
    providerId: "esp32-link",
    capabilities: () => ["process-exec"] as const,
    probe: () => ({ providerId: "esp32-link", available: false, facts: {}, reason: "" }),
    evaluate: () => ({
      satisfied: false,
      enforcement: "none" as const,
      limitations: [],
      reason: "not implemented",
    }),
    deviceCapabilities: () => ["serial"] as const,
  };

  const asDevice: ManagedDeviceProvider = deviceOnly;
  assert.equal(asDevice.providerId, "esp32-link");

  // @ts-expect-error a managed device is not an execution provider and has no confine()
  const asExecutor: ExecutionProvider = deviceOnly;
  void asExecutor;

  const asCapability: CapabilityProvider = deviceOnly;
  assert.deepEqual(asCapability.capabilities(), ["process-exec"]);
});
