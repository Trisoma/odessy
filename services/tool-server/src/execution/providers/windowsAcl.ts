/**
 * The Windows backend: a restricted token whose write capability is an ACL ACE on
 * the workspace, entered through an argv-prefix runner.
 *
 * ## Why this is the first provider that is genuinely `partial`
 *
 * Every other backend either governs every file effect its mode promises or
 * reports itself unavailable. This one is available on Windows and never complete,
 * for two structural reasons:
 *
 * 1. `WRITE_RESTRICTED` must keep `Everyone` in both restricting lists. Without it,
 *    early DLL initialization dies with `0xC0000142` and CNG crashes PowerShell with
 *    `0xE0434352`. So an external NTFS object whose DACL grants Everyone a requested
 *    write right clears both checks and stays writable.
 * 2. NTFS hard links alias a file *object*, not a path. An inheritable workspace ACE
 *    propagated onto an existing hard link edits the one underlying security
 *    descriptor, so the same object becomes writable through an external path.
 *    Rejecting multiply-linked files is not viable — ordinary pnpm workspaces are
 *    full of them.
 *
 * Both are named in `gapLimitations`, which is the mechanism that makes
 * `enforcement` report `partial` rather than `full`. That in turn is what lets a
 * caller that *requires* isolation reject this backend, instead of silently
 * receiving a weaker boundary than it asked for.
 *
 * ## Grant lifecycle
 *
 * This provider passes no `--write-sid`/`--temp-write-sid` pair, so the runner runs
 * its standalone flow: it treats `--temp` as a root, creates a random private child
 * directory, derives that child's own SID, grants it, and removes child and grant on
 * exit. The workspace ACE it creates is standing by design — it is the cross-session
 * reuse cache — and a later `read-only` run stays unaffected, because a
 * write-restricted token grants only what its restricting list carries, so the
 * standing ACE stays inert.
 *
 * Upstream's seam instead owns those grants, which lets a session's temp capability
 * live for the session. That needs a per-call lifecycle, and `ConfineResult` is a
 * pure argv transform with nowhere to put one. Per-invocation temp is not weaker
 * than per-session, so the standalone flow is the correct mapping for a policy that
 * carries no session identity — not a shortcut around one.
 */

import { tmpdir } from "node:os";

import type { ExecutionPolicy, Limitation, RunnerFailureRule } from "../contract.js";
import { discoverHostFacts, type HostFacts } from "../probe.js";
import { assertTempRootOutsideWorkspace } from "../win32/path-boundary.js";
import { RUNNER_ENTRY } from "../win32/runner-entry.js";
import { ConfiningProvider } from "./base.js";

export class WindowsAclProvider extends ConfiningProvider {
  readonly providerId = "windows-acl";

  /**
   * The two documented shortfalls in what the mode promises. Their presence is
   * exactly what makes `enforcement` report `partial`; see the module comment.
   */
  protected override readonly gapLimitations: readonly Limitation[] = [
    "reliance_on_everyone_ace",
    "cannot_restrict_hardlink_alias",
  ];

  /**
   * The dialect a denied file effect speaks under this backend. Four spellings
   * because four layers report it: PowerShell/.NET ("Access to the path '…' is
   * denied."), cmd ("Access is denied."), Node `EACCES`, and Node `EPERM`.
   *
   * Never unioned with another backend's dialect — a signature set is only
   * meaningful for the backend that produced it.
   */
  protected override readonly denialSignatures: readonly string[] = [
    "access is denied",
    "access to the path",
    "permission denied",
    "operation not permitted",
  ];

  /**
   * Exit 127 is the runner's own reserved status and is *gated on the signature*:
   * both must match. Gating on the exit code alone would misclassify a confined
   * command that happens to exit 127, and gating on the signature alone would
   * misclassify a command that merely prints `windows-acl-run: ` — so a command
   * that never ran is never confused with one that ran and failed.
   */
  protected override readonly runnerFailureRules: readonly RunnerFailureRule[] = [
    {
      fatalSignatures: ["windows-acl-run: "],
      allowedExitCodes: [127],
    },
  ];

  constructor(
    private readonly injected?: HostFacts,
    private readonly runnerEntry?: string,
  ) {
    super();
  }

  private host(): HostFacts {
    return this.injected ?? discoverHostFacts();
  }

  protected usable(): readonly [boolean, string] {
    const facts = this.host().windowsAcl;
    return [facts.available, facts.detail];
  }

  /**
   * Every entry here is a boundary this backend's own documentation states.
   *
   * ## Two device limitations are deliberately absent
   *
   * `cannot_restrict_device_access` and `cannot_restrict_ioctl` both follow from
   * the documented mechanism: a device object is ACL-addressable and `\\.\NUL`'s
   * DACL grants Everyone read+write+execute (0x1201BF), while `DeviceIoControl` is
   * not a write access and a `FILE_ANY_ACCESS` ioctl requires no access at all, so
   * neither is intersected by `WRITE_RESTRICTED`. The reasoning is sound, but it is
   * *reasoning* — not a statement this backend's documentation makes — and a
   * limitation set is read as a considered statement about the host.
   *
   * The distinction is not stylistic. `CAPABILITY_LIMITATIONS` in `contract.ts`
   * maps a capability to the limitation that defeats it, and device grants are
   * omitted from that map until the device layer defines what a device grant
   * *means*. A limitation listed here would be one map entry away from silently
   * becoming the reason a future device-access request is granted or refused. Phase
   * 9 decides whether this backend's vocabulary needs the distinction; until then
   * the inference stays prose rather than evidence.
   *
   * The contract's `cannot_restrict_device_access` entry therefore stays declared
   * but unreferenced — correct for a vocabulary awaiting the semantics that give it
   * meaning.
   */
  protected limitations(policy: ExecutionPolicy): readonly Limitation[] {
    return [
      // `WRITE_RESTRICTED` intersects write accesses only. A confined child can
      // read any file its caller can read, and can open sockets.
      "cannot_restrict_reads",
      "cannot_restrict_network",
      "cannot_restrict_process_visibility",
      // Children created with CREATE_NO_WINDOW / CREATE_NEW_CONSOLE die during DLL
      // initialization with STATUS_DLL_INIT_FAILED (0xC0000142), so a confined child
      // shares the host console.
      "no_console_isolation",
      "host_kernel_shared",
      // Reads are not governed, so nothing can be hidden either.
      ...((policy.hiddenPaths ?? []).length > 0 ? (["cannot_hide_paths"] as const) : []),
      ...this.gapLimitations,
    ];
  }

  protected wrap(argv: readonly string[], policy: ExecutionPolicy): readonly string[] {
    const runnerEntry = this.runnerEntry ?? this.host().windowsAcl.runnerEntry ?? RUNNER_ENTRY;
    const tempRoot = tmpdir();

    if (policy.mode === "workspace-write") {
      // The runner rejects an overlapping temp root itself, before granting
      // anything. Checking here too turns that into a provider error with a readable
      // message instead of a runner failure surfaced as exit 127.
      assertTempRootOutsideWorkspace(policy.workspaceRoot, tempRoot);
    }

    return [
      process.execPath,
      runnerEntry,
      "--workspace",
      policy.workspaceRoot,
      "--temp",
      tempRoot,
      "--mode",
      policy.mode,
      "--",
      ...argv,
    ];
  }

  protected override probeFacts(): Readonly<Record<string, unknown>> {
    const facts = this.host().windowsAcl;
    return { available: facts.available, runnerEntry: facts.runnerEntry };
  }

  protected override describe(_policy: ExecutionPolicy): string {
    return (
      "a WRITE_RESTRICTED token whose write capability is an ACL ACE on the workspace; " +
      "reads, network, and process visibility are unrestricted, and the Everyone and " +
      "hard-link boundaries keep the enforcement partial"
    );
  }
}
