/**
 * Shared behaviour for providers that confine by wrapping argv.
 *
 * The backend-specific parts are the facts, the limitation set, and the argv
 * transform. The parts that must never be skipped — the fail-closed check on
 * {@link ConfiningProvider.confine} and the satisfaction rule on
 * {@link ConfiningProvider.evaluate} — live here, in one place, so that adding a
 * backend cannot quietly omit them.
 *
 * The two-way split of reported limitations is the mechanism that keeps
 * `partial` meaningful:
 *
 * `gapLimitations`
 *   A shortfall in the effects the mode *promises*. Its presence makes the
 *   enforcement `partial`, because the backend governs only a subset of what it
 *   claims to.
 *
 * *everything else*
 *   A scope note. True of the mechanism, outside what the file-effect vocabulary
 *   covers, and not a defect: a same-world backend sharing the host kernel is not
 *   failing to do something it promised.
 */

import {
  blockingLimitations,
  isConfinedMode,
  ProviderUnavailableError,
  requiredIsolationIsMet,
  type Capability,
  type ConfineResult,
  type Enforcement,
  type Evaluation,
  type ExecutionPolicy,
  type ExecutionProvider,
  type Limitation,
  type ProbeReport,
  type Requirement,
  type RunnerFailureRule,
} from "../contract.js";

/**
 * What every argv-wrapping backend offers. None of them can be *asked* to govern
 * network or process visibility, because their right vocabularies do not extend
 * there; whether they restrict those anyway is reported through limitations.
 */
export const CONFINEMENT_CAPABILITIES: readonly Capability[] = [
  "process-exec",
  "fs-read",
  "fs-write",
];

export abstract class ConfiningProvider implements ExecutionProvider {
  abstract readonly providerId: string;

  protected readonly gapLimitations: readonly Limitation[] = [];

  /** The selected backend's denial dialect — never a cross-backend union. */
  protected readonly denialSignatures: readonly string[] = [];

  /** Structured evidence that the runner failed before running the command. */
  protected readonly runnerFailureRules: readonly RunnerFailureRule[] = [];

  // ----------------------------------------------------------------------
  // Subclass hooks
  // ----------------------------------------------------------------------

  /**
   * `[usable, reason]` for this host.
   *
   * Must read cached host facts rather than probing, so that `evaluate` stays a
   * pure function and selection does not re-probe per candidate.
   */
  protected abstract usable(): readonly [boolean, string];

  /** The limitations that apply to *this* policy on *this* host. */
  protected abstract limitations(policy: ExecutionPolicy): readonly Limitation[];

  /** The argv to spawn in place of `argv`. */
  protected abstract wrap(argv: readonly string[], policy: ExecutionPolicy): readonly string[];

  /** Backend-specific evidence for the probe report. */
  protected probeFacts(): Readonly<Record<string, unknown>> {
    return {};
  }

  /** One line explaining how this backend confines under `policy`. */
  protected describe(policy: ExecutionPolicy): string {
    return `${this.providerId} confines under ${policy.mode}`;
  }

  // ----------------------------------------------------------------------
  // Shared behaviour
  // ----------------------------------------------------------------------

  capabilities(): readonly Capability[] {
    return CONFINEMENT_CAPABILITIES;
  }

  /** `partial` exactly when a promised effect is not governed. */
  enforcement(limitations: readonly Limitation[]): Enforcement {
    return limitations.some((limitation) => this.gapLimitations.includes(limitation))
      ? "partial"
      : "full";
  }

  probe(): ProbeReport {
    const [usable, reason] = this.usable();
    return {
      providerId: this.providerId,
      available: usable,
      facts: this.probeFacts(),
      reason,
    };
  }

  evaluate(requirement: Requirement): Evaluation {
    const mode = requirement.policy.mode;
    if (!isConfinedMode(mode)) {
      return {
        satisfied: false,
        enforcement: "none",
        limitations: [],
        reason: `${mode} requests no confinement; a confining provider cannot satisfy it`,
      };
    }

    const [usable, reason] = this.usable();
    if (!usable) {
      return { satisfied: false, enforcement: "none", limitations: [], reason };
    }

    const limitations = this.limitations(requirement.policy);
    const enforcement = this.enforcement(limitations);

    if (!requiredIsolationIsMet(requirement, enforcement)) {
      const gaps = limitations
        .filter((limitation) => this.gapLimitations.includes(limitation))
        .sort()
        .join(", ");
      return {
        satisfied: false,
        enforcement,
        limitations,
        reason:
          "required isolation needs an absolute boundary, and this backend reports " +
          `partial enforcement: ${gaps}`,
      };
    }

    const blocking = blockingLimitations(requirement, limitations);
    if (blocking.length > 0) {
      return {
        satisfied: false,
        enforcement,
        limitations,
        reason: `the request needs a dimension this backend cannot restrict: ${blocking.join(", ")}`,
      };
    }

    return {
      satisfied: true,
      enforcement,
      limitations,
      reason: this.describe(requirement.policy),
    };
  }

  /**
   * Wrap `argv`, or refuse.
   *
   * Refusing is the whole point of this method's placement. A backend that is
   * unusable must throw rather than return the caller's argv, because a caller
   * that receives an argv has no way to tell a wrapped one from an unwrapped one.
   */
  confine(argv: readonly string[], policy: ExecutionPolicy): ConfineResult {
    if (!isConfinedMode(policy.mode)) {
      throw new Error(
        `${this.providerId} was asked to confine under mode ${JSON.stringify(policy.mode)}, ` +
          "which requests no confinement",
      );
    }

    const [usable, reason] = this.usable();
    if (!usable) {
      throw new ProviderUnavailableError(policy.mode, reason);
    }

    const limitations = this.limitations(policy);
    return {
      argv: this.wrap(argv, policy),
      enforcement: this.enforcement(limitations),
      limitations,
      denialSignatures: this.denialSignatures,
      runnerFailureRules: this.runnerFailureRules,
      providerId: this.providerId,
    };
  }
}
