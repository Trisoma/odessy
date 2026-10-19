/**
 * Provider registration and capability-driven selection.
 *
 * Selection answers one question: *given what this host actually offers, which
 * provider satisfies this request?* It is a filter, not a ranking. A provider
 * either satisfies the request or it does not, and when the request requires
 * isolation and nothing satisfies it, selection **denies**. It never reaches for
 * a weaker provider to keep the command running, because a requirement that can
 * be silently downgraded is not a requirement.
 *
 * ```text
 * discover -> describe -> check satisfaction -> select or deny
 * ```
 *
 * Caching note, and a deliberate difference from upstream: upstream probes once
 * per provider lifetime and caches the verdict. Odessy caches the *host facts*
 * instead, which is both shared across providers and finer grained. That is what
 * lets `evaluate` stay a pure function of cached facts and the request rather
 * than re-probing per candidate.
 */

import {
  UnsatisfiableRequirementError,
  validateEvaluation,
  type Enforcement,
  type Evaluation,
  type ExecutionProvider,
  type ProbeReport,
  type Requirement,
} from "./contract.js";
import { UnconfinedProvider } from "./providers/index.js";

/**
 * Strongest first. Used only to choose *among* providers that already satisfy the
 * request, so it can never turn an unsatisfied request into a satisfied one.
 */
const ENFORCEMENT_RANK: Readonly<Record<Enforcement, number>> = {
  full: 2,
  partial: 1,
  none: 0,
};

function unsatisfied(reason: string): Evaluation {
  return { satisfied: false, enforcement: "none", limitations: [], reason };
}

/**
 * The chosen provider, plus everything that was considered.
 *
 * The full picture is carried because capability-driven selection has to be
 * explainable: an unexplained selection is indistinguishable from a wrong one.
 */
export interface Selection {
  readonly requirement: Requirement;
  readonly provider: ExecutionProvider;
  readonly evaluation: Evaluation;
  /** Every provider that was considered, and its answer, in registration order. */
  readonly considered: readonly (readonly [string, Evaluation])[];
  /** Providers that failed their own probe, with the evidence. */
  readonly unavailable: readonly ProbeReport[];
}

/** An ordered set of execution providers. */
export class ProviderRegistry {
  private readonly registered: ExecutionProvider[];

  constructor(providers: readonly ExecutionProvider[] = []) {
    this.registered = [...providers];
  }

  register(provider: ExecutionProvider): void {
    this.registered.push(provider);
  }

  providers(): readonly ExecutionProvider[] {
    return [...this.registered];
  }

  /**
   * Choose the provider that satisfies `requirement`.
   *
   * Throws {@link UnsatisfiableRequirementError} when nothing satisfies it,
   * whatever the isolation level. Registering an {@link UnconfinedProvider} is how
   * a caller says "and running unconfined is an acceptable outcome here"; without
   * one, a request that nothing satisfies has no answer.
   */
  select(requirement: Requirement): Selection {
    const considered: [string, Evaluation][] = [];
    const unavailable: ProbeReport[] = [];
    const satisfied: [ExecutionProvider, Evaluation][] = [];

    for (const provider of this.registered) {
      const missing = requirement.capabilities.filter(
        (capability) => !provider.capabilities().includes(capability),
      );
      if (missing.length > 0) {
        considered.push([
          provider.providerId,
          unsatisfied(`does not offer capabilities: ${[...missing].sort().join(", ")}`),
        ]);
        continue;
      }

      const report = provider.probe();
      if (!report.available) {
        unavailable.push(report);
        considered.push([provider.providerId, unsatisfied(`unavailable: ${report.reason}`)]);
        continue;
      }

      const evaluation = provider.evaluate(requirement);
      // A provider that reports `partial` without naming a limitation is
      // defective, not merely unsatisfying. That must surface here rather than be
      // recorded as a normal outcome.
      validateEvaluation(evaluation, provider.providerId);

      considered.push([provider.providerId, evaluation]);
      if (evaluation.satisfied) {
        satisfied.push([provider, evaluation]);
      }
    }

    if (satisfied.length === 0) {
      throw new UnsatisfiableRequirementError(requirement, considered);
    }

    // Strictly greater keeps the earliest registration on a tie.
    let best = satisfied[0] as [ExecutionProvider, Evaluation];
    for (const candidate of satisfied) {
      if (ENFORCEMENT_RANK[candidate[1].enforcement] > ENFORCEMENT_RANK[best[1].enforcement]) {
        best = candidate;
      }
    }

    return {
      requirement,
      provider: best[0],
      evaluation: best[1],
      considered,
      unavailable,
    };
  }
}

/**
 * Build a registry whose last resort is explicitly unconfined execution.
 *
 * The fallback is appended last so that, at equal enforcement rank, a real
 * confinement backend always wins the tie. It can still only be selected when the
 * request does not require isolation.
 */
export function withUnconfinedFallback(
  providers: readonly ExecutionProvider[],
): ProviderRegistry {
  const registry = new ProviderRegistry(providers);
  registry.register(new UnconfinedProvider());
  return registry;
}

/** Render a selection decision for a human. */
export function renderSelection(selection: Selection): string {
  const lines = [
    `selected ${selection.provider.providerId} ` +
      `(enforcement=${selection.evaluation.enforcement})`,
  ];
  if (selection.evaluation.reason) {
    lines.push(`  reason: ${selection.evaluation.reason}`);
  }
  if (selection.evaluation.limitations.length > 0) {
    lines.push(`  limitations: ${[...selection.evaluation.limitations].sort().join(", ")}`);
  }

  lines.push("  considered:");
  for (const [providerId, evaluation] of selection.considered) {
    const mark = evaluation.satisfied ? "satisfied" : "no";
    lines.push(`    ${mark.padEnd(9)} ${providerId}: ${evaluation.reason || evaluation.enforcement}`);
  }

  for (const report of selection.unavailable) {
    lines.push(`    unavailable ${report.providerId}: ${report.reason}`);
  }

  return lines.join("\n");
}
