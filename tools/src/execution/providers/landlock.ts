/**
 * The Landlock backend: a kernel-enforced ruleset installed by a launcher.
 *
 * This is the provider where the ABI decides what is achievable, so the
 * limitation set is *derived* from the negotiated mask rather than declared.
 *
 * The ABI boundaries below are taken from the kernel header as quoted in the
 * launcher's C source, which spells out which bit each ABI introduced:
 *
 * ```text
 * LL_FS_REFER       (1 << 13)   ABI 2
 * LL_FS_TRUNCATE    (1 << 14)   ABI 3   (ABI 4 added TCP bits only)
 * LL_FS_IOCTL_DEV   (1 << 15)   ABI 5
 * ```
 *
 * **The ABI arrives by injection on this side.** Node cannot issue the
 * `landlock_create_ruleset` syscall, so `probeLandlock` reports it as unknown and
 * this provider is unavailable until an ABI source exists. The provider itself is
 * complete and fully tested with an injected ABI; only the input is missing.
 *
 * Reporting a guessed ABI, or deriving one from the kernel release, would break
 * the rule that `partial` must describe a *known* limitation.
 */

import type { ExecutionPolicy, Limitation, RunnerFailureRule } from "../contract.js";
import { discoverHostFacts, type HostFacts } from "../probe.js";
import { landlockCommand } from "../profiles.js";
import { ConfiningProvider } from "./base.js";

export class LandlockProvider extends ConfiningProvider {
  readonly providerId = "landlock";

  /** The ABI that introduced each filesystem right, from the kernel header. */
  static readonly REFER_ABI = 2;
  static readonly TRUNCATE_ABI = 3;
  static readonly IOCTL_DEV_ABI = 5;

  /**
   * Rights an older kernel does not govern at all. Their absence is a shortfall
   * in the file effects the mode promises, so it makes the enforcement partial
   * rather than merely being noted.
   */
  protected override readonly gapLimitations: readonly Limitation[] = [
    "cannot_deny_truncate",
    "cannot_restrict_rename_between_dirs",
    "cannot_restrict_ioctl",
  ];

  protected override readonly denialSignatures: readonly string[] = ["permission denied"];

  protected override readonly runnerFailureRules: readonly RunnerFailureRule[] = [
    {
      fatalSignatures: ["landlock-run: "],
      // A successfully exec'd child may itself return 125, so the exit code
      // alone is never evidence: both signals are required.
      allowedExitCodes: [125],
      informationalLines: ["landlock-run: partial enforcement (older Landlock ABI)"],
    },
  ];

  constructor(
    private readonly injected?: HostFacts,
    private readonly launcher?: string,
  ) {
    super();
  }

  private host(): HostFacts {
    return this.injected ?? discoverHostFacts();
  }

  protected usable(): readonly [boolean, string] {
    const facts = this.host();
    if (facts.landlock.abi === null) {
      return [false, facts.landlock.detail];
    }
    const launcher = facts.landlockLauncher;
    if (launcher.path === null || launcher.enforcement === null) {
      return [false, launcher.detail];
    }
    return [true, launcher.detail];
  }

  protected limitations(policy: ExecutionPolicy): readonly Limitation[] {
    const abi = this.host().landlock.abi;

    // Scope notes: properties of the grants this provider emits, true at every
    // ABI, and outside what the file-effect vocabulary covers.
    const limitations: Limitation[] = [
      // `--ro /` grants read across the host, so reads are unrestricted.
      "cannot_restrict_reads",
      // No network right is requested, and none exists in this mask.
      "cannot_restrict_network",
      // The filesystem mask has no process-visibility right at all.
      "cannot_restrict_process_visibility",
      "host_kernel_shared",
    ];
    // `--ro /` cannot carve a path back out, so hidden paths stay visible.
    if ((policy.hiddenPaths ?? []).length > 0) {
      limitations.push("cannot_hide_paths");
    }

    // Gaps: rights the negotiated ABI does not govern.
    if (abi === null || abi < LandlockProvider.TRUNCATE_ABI) {
      limitations.push("cannot_deny_truncate");
    }
    if (abi === null || abi < LandlockProvider.REFER_ABI) {
      limitations.push("cannot_restrict_rename_between_dirs");
    }
    if (abi === null || abi < LandlockProvider.IOCTL_DEV_ABI) {
      // `--ro /` makes device nodes openable for read, and an unmediated ioctl
      // is how a serial or GPIO device is actually driven.
      limitations.push("cannot_restrict_ioctl");
    }

    return limitations;
  }

  protected wrap(argv: readonly string[], policy: ExecutionPolicy): readonly string[] {
    const executable = this.launcher ?? this.host().landlockLauncher.path;
    if (executable === null || executable === undefined) {
      throw new Error("landlock is usable but has no launcher path");
    }
    return landlockCommand(executable, argv, policy);
  }

  protected override probeFacts(): Readonly<Record<string, unknown>> {
    const facts = this.host();
    return {
      abi: facts.landlock.abi,
      abiDetail: facts.landlock.detail,
      launcher: facts.landlockLauncher.path,
      launcherEnforcement: facts.landlockLauncher.enforcement,
    };
  }

  protected override describe(_policy: ExecutionPolicy): string {
    const facts = this.host();
    return (
      `Landlock ABI ${facts.landlock.abi} with --ro / and the mode's writable grants; ` +
      `the launcher reports ${facts.landlockLauncher.enforcement} enforcement`
    );
  }
}
