/**
 * The explicitly declared absence of confinement.
 *
 * This provider is not a confinement backend. It is the *named, selectable,
 * reportable* statement that no backend is in use, and it exists for one
 * structural reason: selection must be total. A caller that can receive "no
 * provider" is a caller that can forget to handle it. A caller that receives this
 * provider has been told, in the contract's own vocabulary, that `enforcement` is
 * `none`.
 *
 * ## Why this is not "silent unconfined passthrough"
 *
 * The forbidden shape is a provider that returns the caller's argv unchanged
 * while reporting success, so the caller believes a boundary exists. This
 * provider does the opposite: it returns the argv unchanged and reports
 * `enforcement: "none"` with a reason. The absence is loud.
 *
 * The registry is what keeps that honest. It refuses to select this provider when
 * `isolation` is `required`, so the only way to reach unconfined execution is to
 * have asked for something weaker than a guarantee.
 */

import {
  CAPABILITIES,
  type Capability,
  type ConfineResult,
  type Evaluation,
  type ExecutionPolicy,
  type ExecutionProvider,
  type ProbeReport,
  type Requirement,
} from "../contract.js";

const PROVIDER_ID = "unconfined";

export class UnconfinedProvider implements ExecutionProvider {
  readonly providerId = PROVIDER_ID;

  /**
   * Claim everything.
   *
   * This provider governs nothing, but it must survive the registry's capability
   * pre-filter, otherwise a request needing any capability would filter it out
   * and selection would have no unconfined option to offer.
   */
  capabilities(): readonly Capability[] {
    return CAPABILITIES;
  }

  probe(): ProbeReport {
    return {
      providerId: PROVIDER_ID,
      available: true,
      facts: { confinement: "none" },
      reason: "no confinement backend; the absence is reported rather than concealed",
    };
  }

  evaluate(requirement: Requirement): Evaluation {
    if (requirement.isolation === "required") {
      return {
        satisfied: false,
        enforcement: "none",
        limitations: [],
        reason: "isolation is required and this host supplies no confinement backend",
      };
    }
    return {
      satisfied: true,
      enforcement: "none",
      limitations: [],
      reason:
        "running unconfined because isolation is not required " +
        `(requested ${requirement.isolation})`,
    };
  }

  /**
   * Return the argv unchanged, and report that nothing was added.
   *
   * The requested mode is deliberately *not* checked. Refusing here would make an
   * unconfined fallback impossible to express, which is the thing this provider
   * exists to express. The requested mode is instead reported as unmet by
   * `enforcement: "none"`, and the registry decides whether that is acceptable.
   */
  confine(argv: readonly string[], _policy: ExecutionPolicy): ConfineResult {
    return {
      argv,
      enforcement: "none",
      limitations: [],
      denialSignatures: [],
      runnerFailureRules: [],
      providerId: PROVIDER_ID,
    };
  }
}
