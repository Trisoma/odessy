/**
 * The bwrap backend: namespaces plus a read-only host root.
 *
 * bwrap is available on Linux and confines by constructing namespaces rather
 * than by asking the kernel for a per-process ruleset. Its profile is also the
 * strongest of the three with respect to devices: `--dev /dev` mounts a *fresh*
 * minimal devtmpfs over `/dev`, so the host's device nodes — serial ports,
 * cameras, GPIO chips — are not merely unwritable but invisible.
 *
 * Limitations are derived from the profile this provider actually emits:
 *
 * - `--ro-bind / /` grants read across the host, so reads are not restricted.
 * - No network namespace is unshared, so network is not restricted.
 * - `--unshare-pid` *does* restrict host process visibility, so that is
 *   genuinely not a limitation here.
 * - The host kernel is still shared, which is what makes this same-world
 *   confinement rather than isolation.
 */

import type { ExecutionPolicy, Limitation } from "../contract.js";
import { discoverHostFacts, type HostFacts } from "../probe.js";
import { bwrapCommand } from "../profiles.js";
import { ConfiningProvider } from "./base.js";

const USABLE: readonly string[] = ["available_userns", "available_setuid_root"];

export class BwrapProvider extends ConfiningProvider {
  readonly providerId = "bwrap";

  /**
   * bwrap governs every file effect the modes promise, so nothing here is a
   * shortfall. The entries below are scope notes.
   */
  protected override readonly gapLimitations: readonly Limitation[] = [];

  protected override readonly denialSignatures: readonly string[] = ["read-only file system"];

  protected override readonly runnerFailureRules = [
    { fatalSignatures: ["bwrap: "] },
  ] as const;

  constructor(
    private readonly injected?: HostFacts,
    private readonly executable?: string,
  ) {
    super();
  }

  private host(): HostFacts {
    return this.injected ?? discoverHostFacts();
  }

  protected usable(): readonly [boolean, string] {
    const bwrap = this.host().bwrap;
    return [USABLE.includes(bwrap.availability), bwrap.detail];
  }

  protected limitations(policy: ExecutionPolicy): readonly Limitation[] {
    // Reads stay unrestricted outside the hidden paths, which bwrap does hide; the
    // network is cut when the policy asks (`--unshare-net`).
    return [
      "cannot_restrict_reads",
      ...(policy.network === "deny" ? [] : (["cannot_restrict_network"] as const)),
      "host_kernel_shared",
    ];
  }

  protected wrap(argv: readonly string[], policy: ExecutionPolicy): readonly string[] {
    const executable = this.executable ?? this.host().bwrap.path;
    if (executable === null || executable === undefined) {
      throw new Error("bwrap is usable but has no executable path");
    }
    return bwrapCommand(executable, argv, policy);
  }

  protected override probeFacts(): Readonly<Record<string, unknown>> {
    const bwrap = this.host().bwrap;
    return {
      availability: bwrap.availability,
      path: bwrap.path,
      isSetuidRoot: bwrap.isSetuidRoot,
      version: bwrap.version,
    };
  }

  protected override describe(_policy: ExecutionPolicy): string {
    const bwrap = this.host().bwrap;
    return (
      "bwrap namespaces with a read-only host root, a fresh /dev, and a private " +
      `PID namespace (${bwrap.availability})`
    );
  }
}
