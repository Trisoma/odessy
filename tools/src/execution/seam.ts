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
  type IsolationRequirement,
  type Limitation,
  type Mode,
  type NetworkPolicy,
} from "./contract.js";
import type { LimitReport } from "./limits.js";
import { UnconfinedProvider } from "./providers/unconfined.js";
import type { ProviderRegistry } from "./registry.js";
import { extraWritableRoots, hiddenRoots } from "./roots.js";

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
  /** Whether the network was cut, when a policy asked; only when the backend did it. */
  readonly network?: "denied" | "open";
  /** How many paths were hidden; 0 when the backend cannot hide them. */
  readonly hidden_paths?: number;
  readonly limits?: LimitReport;
  readonly limit_exceeded?: string;
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

  const sandbox: SandboxReport = {
    provider_id: result.providerId,
    enforcement: result.enforcement,
    limitations: [...result.limitations].sort(),
  };
  if (confinement === undefined) {
    return { argv: result.argv, sandbox };
  }
  const confined = result.enforcement !== "none";
  const policy = confinement.policy;
  return {
    argv: result.argv,
    sandbox: {
      ...sandbox,
      network:
        confined && policy.network === "deny" && !result.limitations.includes("cannot_restrict_network")
          ? "denied"
          : "open",
      hidden_paths:
        confined && !result.limitations.includes("cannot_hide_paths") ? (policy.hiddenPaths ?? []).length : 0,
    },
  };
}

/** How the authority layer shaped this call's confinement, beyond its mode. */
export interface ConfinementShape {
  readonly network?: NetworkPolicy;
  readonly hidden_paths?: readonly string[];
  readonly extra_writable?: readonly string[];
}

/**
 * Build a host-side confinement from the mode the *authority layer* sent with the call.
 *
 * The host does **not** decide the mode. It answers one question: *can I actually
 * confine under this, and how completely?* That keeps the authority on the peer and
 * this side responsible only for satisfying it — and it is why the mode arrives as
 * data rather than being re-derived from the command here.
 *
 * Selection is necessarily host-side, because only the host knows its own
 * capabilities. When nothing satisfies the requirement this **throws**, which is the
 * fail-closed answer: `isolation: "required"` with `danger-full-access` therefore
 * raises instead of quietly running the command unconfined.
 */
export function confinementFromRegistry(
  mode: Mode,
  isolation: IsolationRequirement,
  workspaceRoot: string,
  registry: ProviderRegistry,
  shape: ConfinementShape = {},
): Confinement {
  const hiddenPaths = hiddenRoots(shape.hidden_paths ?? [], workspaceRoot);
  const selection = registry.select({
    policy: {
      mode,
      workspaceRoot,
      deviceGrants: [],
      network: shape.network === "deny" ? "deny" : "allow",
      hiddenPaths,
      extraWritable: extraWritableRoots(shape.extra_writable ?? [], hiddenPaths),
    },
    isolation,
    capabilities: ["process-exec"],
  });
  return { provider: selection.provider, policy: selection.requirement.policy };
}

/** What the peer sent about how this call should be confined. */
export interface ConfinementRequest extends ConfinementShape {
  readonly mode?: Mode;
  readonly isolation?: IsolationRequirement;
}

/**
 * Resolve the confinement a call asked for, or `undefined` when it asked for none.
 *
 * **The requirement is never inferred.** A `mode` without an `isolation` is refused,
 * rather than defaulted, because the only thing a default could mean is this host
 * guessing how strict the peer is — and a host that guesses permissively has weakened
 * the authority layer's decision. A `required` demand answered with a `partial`
 * backend is precisely that failure, and it is silent.
 *
 * Returning `undefined` is not the same as guessing: it is the honest answer for a
 * caller that expressed no requirement at all, and the run reports `enforcement: "none"`
 * rather than implying a boundary.
 */
export function resolveHostConfinement(
  request: ConfinementRequest,
  workspaceRoot: string,
  registry: ProviderRegistry,
): Confinement | undefined {
  if (request.mode === undefined && request.isolation === undefined) {
    return undefined;
  }
  if (request.mode === undefined || request.isolation === undefined) {
    throw new Error(
      "confinement requires mode and isolation together: a mode alone would leave this " +
        "host to infer how strict the caller is, and inferring permissively would weaken " +
        "a requirement this host was sent with",
    );
  }
  return confinementFromRegistry(request.mode, request.isolation, workspaceRoot, registry, request);
}
