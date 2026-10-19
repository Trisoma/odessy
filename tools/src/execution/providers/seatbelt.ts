/**
 * The macOS Seatbelt backend: a `sandbox-exec` SBPL profile.
 *
 * The profile is allow-default with `file-write*` denied, then exactly the mode's
 * promised write effects allowed back. Two things follow from that shape, and
 * both are reported rather than left for a caller to infer:
 *
 * - Reads are unrestricted, because the profile allows by default.
 * - `ioctl` is unrestricted, because `file-write*` is a different operation in
 *   the Seatbelt vocabulary and nothing denies `ioctl`. On a device node that is
 *   openable for read — which `allow default` permits — an ioctl is how a serial
 *   or GPIO device is actually configured.
 *
 * Both are scope notes rather than shortfalls: they lie outside what the
 * file-effect vocabulary covers, so they do not make the enforcement partial.
 * They do block a request that explicitly asks for network or process-visibility
 * confinement, which is what the shared satisfaction rule is for.
 */

import type { ExecutionPolicy, Limitation } from "../contract.js";
import { discoverHostFacts, type HostFacts } from "../probe.js";
import { seatbeltCommand } from "../profiles.js";
import { ConfiningProvider } from "./base.js";

export class SeatbeltProvider extends ConfiningProvider {
  readonly providerId = "seatbelt";

  /**
   * The profile governs every file effect the modes promise. The entries below
   * are scope notes.
   */
  protected override readonly gapLimitations: readonly Limitation[] = [];

  protected override readonly denialSignatures: readonly string[] = ["operation not permitted"];

  protected override readonly runnerFailureRules = [
    { fatalSignatures: ["sandbox-exec: "] },
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
    const seatbelt = this.host().seatbelt;
    return [seatbelt.available, seatbelt.detail];
  }

  protected limitations(_policy: ExecutionPolicy): readonly Limitation[] {
    // Derived from the profile's shape: what it denies, and what it therefore
    // leaves alone. Not verified against a live macOS host.
    return [
      "cannot_restrict_reads",
      "cannot_restrict_network",
      "cannot_restrict_process_visibility",
      "cannot_restrict_ioctl",
      "host_kernel_shared",
    ];
  }

  protected wrap(argv: readonly string[], policy: ExecutionPolicy): readonly string[] {
    const executable = this.executable ?? this.host().seatbelt.path;
    if (executable === null || executable === undefined) {
      throw new Error("seatbelt is usable but has no executable path");
    }
    return seatbeltCommand(executable, argv, policy);
  }

  protected override probeFacts(): Readonly<Record<string, unknown>> {
    const seatbelt = this.host().seatbelt;
    return { path: seatbelt.path, available: seatbelt.available };
  }

  protected override describe(_policy: ExecutionPolicy): string {
    return "sandbox-exec with file-write* denied and the mode's writable roots allowed";
  }
}
