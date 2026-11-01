/**
 * The shared execution-provider contract (TypeScript view).
 *
 * This mirrors `src/odessy/execution/contract.py`. Both implementations load the
 * same `contract.json`, so the vocabulary and the protocol strings cannot drift
 * between them; `tests/execution-contract.test.ts` asserts the agreement.
 *
 * Scope of `contract.json`: it is the canonical cross-language vocabulary and
 * protocol strings only. It is deliberately not the semantic definition of the
 * layer. ABI-to-limitation mapping, probe logic, selection policy, and
 * per-provider guarantees belong in code and in tests.
 *
 * Invariants carried over from the DeepSeek Harness:
 * - `confine` takes exact argv, never a shell string.
 * - Silent unconfined passthrough is forbidden; a provider that cannot enforce
 *   the requested mode raises instead.
 * - Denial dialects are per backend, never a cross-backend union.
 * - Runner failure is not command denial.
 * - The policy rides the call and is never fixed on the provider.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// --------------------------------------------------------------------------
// Contract file
// --------------------------------------------------------------------------

/** The subset of `contract.json` this module reads. */
interface RawContract {
  version: number;
  modes: readonly string[];
  confined_modes: readonly string[];
  wider_modes: Readonly<Record<string, readonly string[]>>;
  escalation_targets: readonly string[];
  escalation_outcomes: readonly string[];
  escalation_subjects: readonly string[];
  enforcement_levels: readonly string[];
  isolation_requirements: readonly string[];
  network_policies: readonly string[];
  capabilities: readonly string[];
  device_capabilities: readonly string[];
  limitations: readonly string[];
  markers: { denial: string; escalation_hint: string };
}

/**
 * Locate `contract.json` by walking up from this module.
 *
 * The module runs from `tools/src/execution/` during tests and from
 * `tools/dist/src/execution/` after a build, so the depth to the repository root
 * differs. Searching upward avoids depending on which of the two it is.
 */
function findContractPath(): string {
  let directory = path.dirname(fileURLToPath(import.meta.url));
  for (let depth = 0; depth < 10; depth += 1) {
    const candidate = path.join(directory, "src", "odessy", "execution", "contract.json");
    if (fs.existsSync(candidate)) {
      return candidate;
    }
    const parent = path.dirname(directory);
    if (parent === directory) {
      break;
    }
    directory = parent;
  }
  throw new Error(
    "execution contract.json not found; expected it under src/odessy/execution/ " +
      "within 10 directories above this module",
  );
}

function loadContract(): RawContract {
  const contractPath = findContractPath();
  const parsed: unknown = JSON.parse(fs.readFileSync(contractPath, "utf8"));
  if (typeof parsed !== "object" || parsed === null) {
    throw new Error(`${contractPath} must contain a JSON object`);
  }
  return parsed as RawContract;
}

export const CONTRACT: RawContract = loadContract();
export const CONTRACT_VERSION: number = CONTRACT.version;

// --------------------------------------------------------------------------
// Vocabulary
// --------------------------------------------------------------------------

export type Mode = "read-only" | "workspace-write" | "danger-full-access";
export type ConfinedMode = "read-only" | "workspace-write";
export type Enforcement = "full" | "partial" | "none";
export type IsolationRequirement = "required" | "preferred" | "not-required";
export type NetworkPolicy = "allow" | "deny";
export type EscalationOutcome = "allowed-once" | "rejected" | "cancelled" | "unavailable";
export type EscalationSubject = "command" | "operation";

export type Capability =
  | "process-exec"
  | "fs-read"
  | "fs-write"
  | "network"
  | "process-visibility";

export type DeviceCapability =
  | "gpio"
  | "pwm"
  | "adc"
  | "serial"
  | "spi"
  | "i2c"
  | "can"
  | "usb"
  | "camera";

export type Limitation =
  | "cannot_deny_truncate"
  | "cannot_restrict_rename_between_dirs"
  | "cannot_restrict_reads"
  | "cannot_restrict_network"
  | "cannot_restrict_ioctl"
  | "cannot_restrict_process_visibility"
  | "cannot_restrict_device_access"
  | "cannot_restrict_hardlink_alias"
  | "reliance_on_everyone_ace"
  | "no_console_isolation"
  | "host_kernel_shared"
  | "cannot_hide_paths";

export const MODES = [
  "read-only",
  "workspace-write",
  "danger-full-access",
] as const satisfies readonly Mode[];

export const CONFINED_MODES = ["read-only", "workspace-write"] as const satisfies readonly ConfinedMode[];

/**
 * The escalation ladder: a closed table of strictly wider modes. A request that
 * does not widen is rejected without prompting a human.
 */
export const WIDER_MODES: Readonly<Record<ConfinedMode, readonly Mode[]>> = {
  "read-only": ["workspace-write", "danger-full-access"],
  "workspace-write": ["danger-full-access"],
};

export const ESCALATION_TARGETS = [
  "workspace-write",
  "danger-full-access",
] as const satisfies readonly Mode[];

export const ESCALATION_OUTCOMES = [
  "allowed-once",
  "rejected",
  "cancelled",
  "unavailable",
] as const satisfies readonly EscalationOutcome[];

export const ESCALATION_SUBJECTS = ["command", "operation"] as const satisfies readonly EscalationSubject[];

export const ENFORCEMENT_LEVELS = ["full", "partial", "none"] as const satisfies readonly Enforcement[];

export const ISOLATION_REQUIREMENTS = [
  "required",
  "preferred",
  "not-required",
] as const satisfies readonly IsolationRequirement[];

export const NETWORK_POLICIES = ["allow", "deny"] as const satisfies readonly NetworkPolicy[];

export const CAPABILITIES = [
  "process-exec",
  "fs-read",
  "fs-write",
  "network",
  "process-visibility",
] as const satisfies readonly Capability[];

export const DEVICE_CAPABILITIES = [
  "gpio",
  "pwm",
  "adc",
  "serial",
  "spi",
  "i2c",
  "can",
  "usb",
  "camera",
] as const satisfies readonly DeviceCapability[];

export const LIMITATIONS = [
  "cannot_deny_truncate",
  "cannot_restrict_rename_between_dirs",
  "cannot_restrict_reads",
  "cannot_restrict_network",
  "cannot_restrict_ioctl",
  "cannot_restrict_process_visibility",
  "cannot_restrict_device_access",
  "cannot_restrict_hardlink_alias",
  "reliance_on_everyone_ace",
  "no_console_isolation",
  "host_kernel_shared",
  "cannot_hide_paths",
] as const satisfies readonly Limitation[];

/**
 * Narrow a mode to the ones a confining profile exists for.
 *
 * A type predicate rather than a membership test, because the const tuple
 * narrows `includes` to its own literals and would reject a widened `Mode`.
 */
export function isConfinedMode(mode: Mode): mode is ConfinedMode {
  return mode === "read-only" || mode === "workspace-write";
}

/**
 * Which capability a scope limitation defeats.
 *
 * A limitation only blocks a request that actually asks for the dimension it
 * concerns: a backend that cannot restrict network is perfectly adequate for a
 * request that never claimed to need network confinement.
 */
const CAPABILITY_LIMITATIONS: readonly (readonly [Capability, Limitation])[] = [
  ["network", "cannot_restrict_network"],
  ["process-visibility", "cannot_restrict_process_visibility"],
];

/**
 * The subset of `limitations` that defeats *this* request.
 *
 * Device grants are deliberately not consulted yet. Whether an inability to
 * restrict device access defeats a request depends on the device-grant
 * semantics, which are defined with the device layer; inventing them now would
 * be guessing.
 */
export function blockingLimitations(
  requirement: Requirement,
  limitations: readonly Limitation[],
): readonly Limitation[] {
  return CAPABILITY_LIMITATIONS.filter(
    ([capability, limitation]) =>
      requirement.capabilities.includes(capability) && limitations.includes(limitation),
  )
    .map(([, limitation]) => limitation)
    .sort();
}

/**
 * Whether `enforcement` honours a *required* isolation request.
 *
 * `partial` means the backend governs only a subset of the effects the mode
 * promises. That is an acceptable answer to a request that merely prefers
 * isolation, and an unacceptable one to a request that requires it.
 */
export function requiredIsolationIsMet(
  requirement: Requirement,
  enforcement: Enforcement,
): boolean {
  if (requirement.isolation !== "required") {
    return true;
  }
  return enforcement === "full";
}

// --------------------------------------------------------------------------
// Failure modes
// --------------------------------------------------------------------------

/** Carried through the MCP error channel so "no confinement" is distinguishable from "command failed". */
export const PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE";

/** Carried when isolation was required and nothing satisfied it. */
export const REQUIREMENT_UNSATISFIED = "REQUIREMENT_UNSATISFIED";

export class ProviderError extends Error {
  readonly code: string;

  constructor(message: string, code = "PROVIDER_ERROR") {
    super(message);
    this.name = new.target.name;
    this.code = code;
  }
}

/**
 * No usable backend exists for the requested mode.
 *
 * Raised rather than running the command unconfined: refusing to run is always
 * preferable to running while claiming a boundary that does not exist.
 */
export class ProviderUnavailableError extends ProviderError {
  readonly mode: Mode;
  readonly detail: string | undefined;

  constructor(mode: Mode, detail?: string) {
    let message =
      `no confinement backend is usable for mode "${mode}" on this host; ` +
      "refusing to run the command unconfined";
    if (detail !== undefined) {
      message = `${message}. Runner failure: ${detail}`;
    }
    super(message, PROVIDER_UNAVAILABLE);
    this.mode = mode;
    this.detail = detail;
  }
}

/**
 * Isolation was required, and no provider could supply it.
 *
 * Carries every candidate's evaluation so the caller can explain what was
 * discovered and why it was insufficient. Raised instead of choosing a weaker
 * provider: a requirement that can be silently downgraded is not a requirement.
 */
export class UnsatisfiableRequirementError extends ProviderError {
  readonly requirement: Requirement;
  readonly evaluations: readonly (readonly [string, Evaluation])[];

  constructor(requirement: Requirement, evaluations: readonly (readonly [string, Evaluation])[]) {
    const detail = evaluations
      .map(([providerId, evaluation]) => `${providerId}: ${evaluation.reason || evaluation.enforcement}`)
      .join("; ");
    super(
      "isolation was required but no available provider satisfies the requested policy " +
        `(mode=${JSON.stringify(requirement.policy.mode)}, ` +
        `capabilities=[${[...requirement.capabilities].sort().join(", ")}]). ` +
        (detail || "no providers registered"),
      REQUIREMENT_UNSATISFIED,
    );
    this.requirement = requirement;
    this.evaluations = evaluations;
  }
}

/** A provider violated the contract. An implementation defect, not a policy outcome. */
export class ProviderContractError extends ProviderError {
  constructor(message: string) {
    super(message, "PROVIDER_CONTRACT_VIOLATION");
  }
}

// --------------------------------------------------------------------------
// Data shapes
// --------------------------------------------------------------------------

/**
 * The file-effect policy one confined execution runs under.
 *
 * `deviceGrants` names device nodes the confined process may reach. It is part
 * of the policy rather than only of the authority layer because a confined
 * process that can open a device node can drive the attached hardware without
 * any tool call, and therefore without the authority layer being consulted.
 */
export interface ExecutionPolicy {
  mode: Mode;
  workspaceRoot: string;
  deviceGrants: readonly string[];
  /** `deny` asks the backend to cut the network; best effort, reported through
   * `cannot_restrict_network`. Absent means `allow`. */
  network?: NetworkPolicy;
  /** Absolute paths the confined process must not see, already expanded and
   * checked by the executor. A backend that cannot hide them reports
   * `cannot_hide_paths`. */
  hiddenPaths?: readonly string[];
  /** Absolute folders added to `workspace-write`'s writable roots. */
  extraWritable?: readonly string[];
}

/** What a consumer is asking for, including how strict it is about it. */
export interface Requirement {
  policy: ExecutionPolicy;
  isolation: IsolationRequirement;
  capabilities: readonly Capability[];
}

/**
 * Evidence that the confinement runner failed before executing the command.
 *
 * A consumer applies `allowedExitCodes` when present, removes
 * `informationalLines` by exact line equality, then matches `fatalSignatures`
 * case-insensitively within each remaining stderr line. Exit status alone never
 * proves runner failure.
 */
export interface RunnerFailureRule {
  fatalSignatures: readonly string[];
  allowedExitCodes?: readonly number[];
  informationalLines?: readonly string[];
}

/**
 * The wrapped argv a caller should spawn, plus what that wrapping achieves.
 *
 * `enforcement` is the *actual* completeness, which may be weaker than what
 * `evaluate` predicted. A weaker actual result must be surfaced, never swallowed.
 */
export interface ConfineResult {
  argv: readonly string[];
  enforcement: Enforcement;
  limitations: readonly Limitation[];
  denialSignatures: readonly string[];
  runnerFailureRules: readonly RunnerFailureRule[];
  providerId: string;
}

/**
 * One provider's answer to one requirement.
 *
 * `satisfied` is the answer; the rest is the explanation. `limitations`
 * describes the shortfall under `partial`, and the scope of the mechanism under
 * `full` (for example `host_kernel_shared`, a property of every same-world
 * backend and not a defect).
 */
export interface Evaluation {
  satisfied: boolean;
  enforcement: Enforcement;
  limitations: readonly Limitation[];
  reason: string;
}

/**
 * What a probe discovered about this host for one provider.
 *
 * Probe reports are always surfaced to the user: capability-driven selection can
 * otherwise surprise, and an unexplained selection is indistinguishable from a
 * wrong one.
 */
export interface ProbeReport {
  providerId: string;
  available: boolean;
  facts: Readonly<Record<string, unknown>>;
  reason: string;
}

// --------------------------------------------------------------------------
// Contract enforcement
// --------------------------------------------------------------------------

/**
 * Assert the `partial` invariant on an evaluation.
 *
 * `partial` must describe a known limitation. Reporting `partial` without naming
 * what is partial tells the caller something is missing without saying what,
 * which is indistinguishable from "the mechanism merely exists".
 */
export function validateEvaluation(evaluation: Evaluation, providerId: string): void {
  if (evaluation.enforcement === "partial" && evaluation.limitations.length === 0) {
    throw new ProviderContractError(
      `provider "${providerId}" reported enforcement "partial" with no declared ` +
        "limitation; partial must describe a known limitation",
    );
  }
}

/** Assert the `partial` invariant on an actual confinement result. */
export function validateConfineResult(result: ConfineResult): void {
  if (result.enforcement === "partial" && result.limitations.length === 0) {
    throw new ProviderContractError(
      `provider "${result.providerId}" reported enforcement "partial" with no declared ` +
        "limitation; partial must describe a known limitation",
    );
  }
}

// --------------------------------------------------------------------------
// Provider protocols
// --------------------------------------------------------------------------

/**
 * Base protocol: what exists here, and whether it satisfies a request.
 *
 * There is deliberately no static `guarantees()` accessor. A guarantee is only
 * meaningful relative to a request, so it is produced by `evaluate` and, for an
 * actual run, by `confine`.
 *
 * Cost discipline: `probe` is the only member that touches the host, and its
 * result is cached per process. `evaluate` must be a pure function of cached
 * facts and the requirement, and must never re-run a probe.
 */
export interface CapabilityProvider {
  readonly providerId: string;
  /** Coarse static declaration used to pre-filter candidates. */
  capabilities(): readonly Capability[];
  /** Discover whether this provider is usable on this host. */
  probe(): ProbeReport;
  /** Decide whether this provider satisfies `requirement`, and why. */
  evaluate(requirement: Requirement): Evaluation;
}

/**
 * A provider that runs confined work.
 *
 * `confine` takes exact argv rather than a shell string and returns the argv to
 * spawn in its place. It does not spawn: process lifecycle stays with the
 * executor, which keeps the provider stateless and directly testable.
 */
export interface ExecutionProvider extends CapabilityProvider {
  /** Wrap `argv` for confinement, or raise rather than pass through. */
  confine(argv: readonly string[], policy: ExecutionPolicy): ConfineResult;
}

/**
 * A device Odessy manages across a link, not an execution environment.
 *
 * There is deliberately no `confine` and no `execute`. An MCU has no processes
 * and Odessy never runs code on one: the host reasons and orchestrates, and the
 * device performs low-level real-time work. Device access is a permission surface
 * under the authority layer, and the ability of confined process code to reach
 * the device directly is governed by the isolation layer.
 */
export interface ManagedDeviceProvider extends CapabilityProvider {
  /** Which device-level capabilities this provider offers. */
  deviceCapabilities(): readonly DeviceCapability[];
}

// --------------------------------------------------------------------------
// Protocol strings
// --------------------------------------------------------------------------

/** The marker a backend emits for a file effect it denied under `mode`. */
export function sandboxDenialMarker(mode: Mode): string {
  return CONTRACT.markers.denial.replace("{mode}", mode);
}

/** The marker telling the model an escalation retry is available once. */
export function escalationHint(subject: EscalationSubject): string {
  return CONTRACT.markers.escalation_hint.replace("{subject}", subject);
}
