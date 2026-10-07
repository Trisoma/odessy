/**
 * The executor seam: the one place a spawned command is routed through a provider.
 *
 * This mirrors `CommandBridge._confine` on the Python side. Both languages must
 * reach the same decision from the same inputs, so the defaulting rule lives in
 * exactly one function per language and nowhere else.
 *
 * The rule is that **every** run goes through `confine()`, including a run with no
 * backend configured. A bridge that skipped the call when no backend was present
 * would leave the caller to infer enforcement from silence, and "no boundary" and
 * "a boundary I forgot to report" would become indistinguishable.
 */

import {
  type ConfineResult,
  type Enforcement,
  type ExecutionPolicy,
  type ExecutionProvider,
  type Limitation,
} from "./contract.js";
import { UnconfinedProvider } from "./providers/unconfined.js";

/** A backend together with the policy it confines under. */
export interface Confinement {
  readonly provider: ExecutionProvider;
  readonly policy: ExecutionPolicy;
}

/** What confinement achieved for one run, in the contract's own vocabulary. */
export interface SandboxReport {
  readonly provider_id: string;
  readonly enforcement: Enforcement;
  readonly limitations: readonly Limitation[];
}

/** The named absence of a backend, so every run still passes through `confine()`. */
const UNCONFINED = new UnconfinedProvider();

/**
 * The policy used when no backend is configured.
 *
 * Its mode is inert: `UnconfinedProvider` ignores it and returns the argv
 * unchanged. `read-only` is chosen because it is the most restrictive value, so
 * that a backend configured without an explicit policy fails closed rather than
 * over-granting.
 */
function inertPolicy(workspaceRoot: string): ExecutionPolicy {
  return { mode: "read-only", workspaceRoot, deviceGrants: [] };
}

/** One run's confinement decision: the argv to spawn, and what that achieves. */
export interface ConfinedArgv {
  readonly argv: readonly string[];
  readonly sandbox: SandboxReport;
}

/**
 * Route `argv` through the configured backend.
 *
 * Throws rather than falling back when the backend refuses: a caller that asked
 * for a boundary must never receive an unconfined run instead, because the
 * resulting record would be indistinguishable from one where the boundary held.
 */
export function confineArgv(
  confinement: Confinement | undefined,
  argv: readonly string[],
  workspaceRoot: string,
): ConfinedArgv {
  const result: ConfineResult =
    confinement === undefined
      ? UNCONFINED.confine(argv, inertPolicy(workspaceRoot))
      : confinement.provider.confine(argv, confinement.policy);

  return {
    argv: result.argv,
    sandbox: {
      provider_id: result.providerId,
      enforcement: result.enforcement,
      limitations: [...result.limitations].sort(),
    },
  };
}
