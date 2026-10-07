import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  ProviderUnavailableError,
  type Enforcement,
  type ExecutionPolicy,
  type Requirement,
} from "../src/execution/contract.js";
import { classifyRunnerFailure, matchesSignature } from "../src/execution/diagnostics.js";
import {
  PROBE_MARKER_READONLY,
  PROBE_MARKER_WRITABLE,
  bwrapProbeArgv,
  bwrapProbeScript,
  classifyBwrapAvailability,
  interpretBwrapProbe,
  interpretWindowsAclProbe,
  windowsAclProbeArgv,
  type BwrapAvailability,
  type HostFacts,
} from "../src/execution/probe.js";
import { BwrapProvider } from "../src/execution/providers/bwrap.js";
import { LandlockProvider } from "../src/execution/providers/landlock.js";
import { SeatbeltProvider } from "../src/execution/providers/seatbelt.js";
import { WindowsAclProvider } from "../src/execution/providers/windowsAcl.js";

const LAUNCHER = "/usr/bin/landlock-run";

interface FactsOptions {
  abi?: number | null;
  launcherEnforcement?: Enforcement | null;
  launcherPath?: string | null;
  bwrapAvailability?: BwrapAvailability;
  bwrapPath?: string | null;
  seatbeltAvailable?: boolean;
  seatbeltPath?: string | null;
  windowsAclAvailable?: boolean;
  windowsAclRunnerEntry?: string | null;
}

function makeFacts(options: FactsOptions = {}): HostFacts {
  const {
    abi = 5,
    launcherEnforcement = "full",
    launcherPath = LAUNCHER,
    bwrapAvailability = "unavailable_no_binary",
    bwrapPath = null,
    seatbeltAvailable = false,
    seatbeltPath = null,
    windowsAclAvailable = false,
    windowsAclRunnerEntry = null,
  } = options;

  return {
    platform: "linux",
    system: "Linux",
    release: "6.7.0",
    machine: "aarch64",
    landlock: { abi, detail: `stub: ABI ${abi}` },
    landlockLauncher: {
      path: launcherPath,
      enforcement: launcherEnforcement,
      detail: "stub launcher",
    },
    bwrap: {
      availability: bwrapAvailability,
      path: bwrapPath,
      isSetuidRoot: false,
      version: "bubblewrap 0.8.0",
      detail: "stub bwrap",
    },
    seatbelt: { available: seatbeltAvailable, path: seatbeltPath, detail: "stub seatbelt" },
    windowsAcl: {
      available: windowsAclAvailable,
      runnerEntry: windowsAclRunnerEntry,
      detail: "stub windows acl",
    },
    userNamespaces: {
      unprivilegedUsernsClone: null,
      maxUserNamespaces: null,
      apparmorRestrictUnprivilegedUserns: null,
    },
    deviceNodes: {},
  };
}

function requirement(
  options: {
    mode?: ExecutionPolicy["mode"];
    isolation?: Requirement["isolation"];
    capabilities?: Requirement["capabilities"];
  } = {},
): Requirement {
  return {
    policy: { mode: options.mode ?? "workspace-write", workspaceRoot: "/work", deviceGrants: [] },
    isolation: options.isolation ?? "preferred",
    capabilities: options.capabilities ?? ["process-exec"],
  };
}

function usableBwrap(): BwrapProvider {
  return new BwrapProvider(
    makeFacts({ bwrapAvailability: "available_userns", bwrapPath: "/usr/bin/bwrap" }),
    "/usr/bin/bwrap",
  );
}

function usableLandlock(options: FactsOptions = {}): LandlockProvider {
  return new LandlockProvider(makeFacts(options), LAUNCHER);
}

function usableSeatbelt(): SeatbeltProvider {
  return new SeatbeltProvider(
    makeFacts({ seatbeltAvailable: true, seatbeltPath: "/usr/bin/sandbox-exec" }),
    "/usr/bin/sandbox-exec",
  );
}

const RUNNER = "C:\\runner\\runner.js";

function usableWindowsAcl(options: FactsOptions = {}): WindowsAclProvider {
  return new WindowsAclProvider(
    makeFacts({ windowsAclAvailable: true, windowsAclRunnerEntry: RUNNER, ...options }),
    RUNNER,
  );
}

// --------------------------------------------------------------------------
// The Landlock limitation set is derived from the negotiated ABI
// --------------------------------------------------------------------------

const GAPS_BY_ABI: Readonly<Record<number, readonly string[]>> = {
  1: ["cannot_deny_truncate", "cannot_restrict_rename_between_dirs", "cannot_restrict_ioctl"],
  2: ["cannot_deny_truncate", "cannot_restrict_ioctl"],
  3: ["cannot_restrict_ioctl"],
  4: ["cannot_restrict_ioctl"],
  5: [],
};

test("landlock gaps track the kernel header", () => {
  // Each boundary comes from the header, so each must be pinned. A limitation
  // that drifts from the ABI it describes reads as a considered statement about
  // this host when it is not one.
  const gaps = ["cannot_deny_truncate", "cannot_restrict_rename_between_dirs", "cannot_restrict_ioctl"];

  for (const [abi, expected] of Object.entries(GAPS_BY_ABI)) {
    const evaluation = usableLandlock({ abi: Number(abi) }).evaluate(requirement());
    const reported = evaluation.limitations.filter((limitation) => gaps.includes(limitation));
    assert.deepEqual([...reported].sort(), [...expected].sort(), `ABI ${abi}`);
  }
});

test("landlock ABI five reports full enforcement", () => {
  const evaluation = usableLandlock({ abi: 5 }).evaluate(requirement());

  assert.equal(evaluation.enforcement, "full");
  assert.equal(evaluation.satisfied, true);
});

test("landlock older ABIs report partial", () => {
  for (const abi of [1, 2, 3, 4]) {
    assert.equal(usableLandlock({ abi }).evaluate(requirement()).enforcement, "partial", `ABI ${abi}`);
  }
});

// --------------------------------------------------------------------------
// Scope notes versus shortfalls
// --------------------------------------------------------------------------

test("a backend with no shortfall reports full while still listing scope notes", () => {
  for (const provider of [usableBwrap(), usableSeatbelt()]) {
    const evaluation = provider.evaluate(requirement());
    assert.equal(evaluation.enforcement, "full", provider.providerId);
    assert.ok(evaluation.limitations.length > 0, "scope notes are still reported under full");
  }
});

test("bwrap reports no process-visibility limitation", () => {
  // --unshare-pid genuinely hides host processes, so it is not a gap. Reporting
  // one anyway would be as dishonest as omitting a real one.
  assert.ok(!usableBwrap().evaluate(requirement()).limitations.includes("cannot_restrict_process_visibility"));
});

test("bwrap reports no device limitation", () => {
  // A fresh /dev shadows the host's device nodes entirely.
  assert.ok(!usableBwrap().evaluate(requirement()).limitations.includes("cannot_restrict_device_access"));
});

test("a scope note does not block a request that ignored it", () => {
  assert.equal(usableBwrap().evaluate(requirement()).satisfied, true);
});

test("a scope note blocks a request that asked for that dimension", () => {
  const evaluation = usableBwrap().evaluate(
    requirement({ capabilities: ["process-exec", "network"] }),
  );

  assert.equal(evaluation.satisfied, false);
  assert.match(evaluation.reason, /cannot_restrict_network/);
});

// --------------------------------------------------------------------------
// required means an absolute boundary
// --------------------------------------------------------------------------

test("required isolation is not met by partial enforcement", () => {
  for (const abi of [1, 2, 3, 4]) {
    const evaluation = usableLandlock({ abi }).evaluate(requirement({ isolation: "required" }));
    assert.equal(evaluation.satisfied, false, `ABI ${abi}`);
    assert.match(evaluation.reason, /required isolation/);
  }
});

test("required isolation is met by full enforcement", () => {
  assert.equal(usableLandlock({ abi: 5 }).evaluate(requirement({ isolation: "required" })).satisfied, true);
});

test("preferring isolation accepts partial enforcement", () => {
  assert.equal(usableLandlock({ abi: 3 }).evaluate(requirement({ isolation: "preferred" })).satisfied, true);
});

// --------------------------------------------------------------------------
// Fail-closed
// --------------------------------------------------------------------------

test("an unusable backend refuses to confine", () => {
  // It must throw, never return the argv: a caller that receives an argv has no
  // way to tell a wrapped one from an unwrapped one.
  const unusable = [
    new BwrapProvider(makeFacts({ bwrapAvailability: "unavailable_no_binary" })),
    new BwrapProvider(makeFacts({ bwrapAvailability: "unavailable_binary_unusable" })),
    new LandlockProvider(makeFacts({ abi: null, launcherEnforcement: null, launcherPath: null })),
    new LandlockProvider(makeFacts({ abi: 5, launcherEnforcement: null })),
    new SeatbeltProvider(makeFacts({ seatbeltAvailable: false })),
    new WindowsAclProvider(makeFacts({ windowsAclAvailable: false })),
  ];

  for (const provider of unusable) {
    assert.throws(
      () => provider.confine(["echo", "hi"], { mode: "read-only", workspaceRoot: "/work", deviceGrants: [] }),
      ProviderUnavailableError,
      provider.providerId,
    );
    assert.equal(provider.probe().available, false, provider.providerId);
  }
});

test("an unusable backend is never satisfied", () => {
  const evaluation = new BwrapProvider(makeFacts()).evaluate(requirement());

  assert.equal(evaluation.satisfied, false);
  assert.equal(evaluation.enforcement, "none");
});

test("confining under full access is a caller error", () => {
  assert.throws(
    () => usableBwrap().confine(["echo"], { mode: "danger-full-access", workspaceRoot: "/work", deviceGrants: [] }),
    /requests no confinement/,
  );
});

// --------------------------------------------------------------------------
// Confinement output
// --------------------------------------------------------------------------

test("confine returns the wrapped argv and the backend dialect", () => {
  const result = usableBwrap().confine(["echo", "hi"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  assert.equal(result.providerId, "bwrap");
  assert.equal(result.argv[0], "/usr/bin/bwrap");
  assert.deepEqual(result.argv.slice(-2), ["echo", "hi"]);
  assert.deepEqual(result.denialSignatures, ["read-only file system"]);
  assert.equal(result.enforcement, "full");
});

test("landlock confine declares the launcher failure rule", () => {
  // Both signals are required: exit 125 *and* a fatal landlock-run: line. A
  // successfully exec'd child may itself return 125.
  const result = usableLandlock({ abi: 5 }).confine(["echo", "hi"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  assert.deepEqual(result.argv.slice(0, 4), [LAUNCHER, "--ro", "/", "--rw"]);
  assert.deepEqual(result.denialSignatures, ["permission denied"]);

  const rule = result.runnerFailureRules[0];
  assert.ok(rule);
  assert.deepEqual(rule.allowedExitCodes, [125]);
  assert.deepEqual(rule.fatalSignatures, ["landlock-run: "]);

  const notice = "landlock-run: partial enforcement (older Landlock ABI)\n";
  assert.equal(classifyRunnerFailure(125, notice, result.runnerFailureRules), undefined);
  assert.notEqual(classifyRunnerFailure(125, "landlock-run: ruleset error\n", result.runnerFailureRules), undefined);
  assert.equal(classifyRunnerFailure(0, "", result.runnerFailureRules), undefined);
});

test("a denial is distinguished from a runner failure", () => {
  const result = usableLandlock({ abi: 5 }).confine(["echo"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  const stderr = "sh: cannot create /work/x: Permission denied\n";
  assert.equal(matchesSignature(1, stderr, result.denialSignatures), true);
  assert.equal(classifyRunnerFailure(1, stderr, result.runnerFailureRules), undefined);
});

// --------------------------------------------------------------------------
// The confinement probe
// --------------------------------------------------------------------------

test("the probe argv attempts a prohibited write inside the profile", () => {
  const argv = bwrapProbeArgv("/usr/bin/bwrap", "/tmp/odessy-probe/write");

  assert.equal(argv[0], "/usr/bin/bwrap");
  assert.ok(argv.includes("--ro-bind"));
  assert.ok(argv.includes("--die-with-parent"));
  assert.ok(argv.indexOf("--") < argv.indexOf("/bin/sh"));
  assert.ok((argv.at(-1) as string).includes("/tmp/odessy-probe/write"));
});

test("the probe script reports both outcomes distinguishably", () => {
  const script = bwrapProbeScript("/tmp/odessy-probe/write");

  assert.ok(script.includes(PROBE_MARKER_READONLY));
  assert.ok(script.includes(PROBE_MARKER_WRITABLE));
  assert.notEqual(PROBE_MARKER_READONLY, PROBE_MARKER_WRITABLE);
});

test("the probe script quotes the target", () => {
  const script = bwrapProbeScript("/tmp/a path with spaces/write");

  assert.ok(script.includes("'/tmp/a path with spaces/write'"));
});

test("a demonstrated denial is the only outcome that reads as confined", () => {
  assert.deepEqual(interpretBwrapProbe(PROBE_MARKER_READONLY, "", 0, false), [
    0,
    "the read-only profile denied a write, so the boundary is real",
  ]);

  const [status, detail] = interpretBwrapProbe(PROBE_MARKER_WRITABLE, "", 0, true);
  assert.equal(status, 1);
  assert.match(detail, /did NOT deny the write/);

  // Residue alone is enough: the write happened even if the marker lied.
  assert.equal(interpretBwrapProbe("", "", 0, true)[0], 1);

  // Neither marker means the command never ran, which is not a denial.
  const [missing] = interpretBwrapProbe("", "bwrap: failed", 1, false);
  assert.equal(missing, null);
});

test("bwrap availability maps to four distinguishable states", () => {
  assert.equal(classifyBwrapAvailability(null, null, false), "unavailable_no_binary");
  assert.equal(classifyBwrapAvailability("/usr/bin/bwrap", 0, false), "available_userns");
  assert.equal(classifyBwrapAvailability("/usr/bin/bwrap", 0, true), "available_setuid_root");
  assert.equal(classifyBwrapAvailability("/usr/bin/bwrap", 1, false), "unavailable_binary_unusable");
  assert.equal(classifyBwrapAvailability("/usr/bin/bwrap", null, false), "unavailable_binary_unusable");
});

// --------------------------------------------------------------------------
// The Windows ACL backend is available and incomplete
// --------------------------------------------------------------------------

test("the Windows backend is the first to report partial while available", () => {
  // Every other backend is either complete or unavailable. This one is both
  // available and short of what the mode promises, and that combination is the
  // whole reason `enforcement` is a reported fact rather than a boolean.
  const provider = usableWindowsAcl();
  const evaluation = provider.evaluate(requirement());

  assert.equal(provider.probe().available, true);
  assert.equal(evaluation.satisfied, true);
  assert.equal(evaluation.enforcement, "partial");
});

test("both documented Windows boundaries are named as gaps", () => {
  const result = usableWindowsAcl().confine(["echo"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  // Everyone must stay in both restricting lists (early DLL init dies with
  // 0xC0000142 without it), and NTFS hard links alias a file object rather than
  // a path. Both are promises the mode makes and this backend cannot keep.
  assert.ok(result.limitations.includes("reliance_on_everyone_ace"));
  assert.ok(result.limitations.includes("cannot_restrict_hardlink_alias"));
  assert.equal(result.enforcement, "partial");
});

test("the Windows scope notes match the backend's documented reach", () => {
  const result = usableWindowsAcl().confine(["echo"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  for (const note of [
    "cannot_restrict_reads",
    "cannot_restrict_network",
    "cannot_restrict_process_visibility",
    "no_console_isolation",
    "host_kernel_shared",
  ] as const) {
    assert.ok(result.limitations.includes(note), `${note} is missing`);
  }
});

test("inferred device and ioctl limitations stay out of the formal set", () => {
  // Both follow from the documented mechanism, but neither is a boundary this
  // backend's documentation states — and a limitation set is read as a considered
  // statement about the host, so an inference must not be promoted into evidence.
  //
  // The hazard is concrete: `CAPABILITY_LIMITATIONS` maps a capability to the
  // limitation that defeats it, and device grants are omitted from that map until
  // the device layer defines what a device grant means. A device limitation in this
  // list would be one map entry away from deciding whether a future device-access
  // request is granted or refused. Phase 9 makes that call; until then the reasoning
  // lives in prose.
  const result = usableWindowsAcl().confine(["echo"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  assert.equal(result.limitations.includes("cannot_restrict_device_access"), false);
  assert.equal(result.limitations.includes("cannot_restrict_ioctl"), false);
});

test("required isolation rejects the partial Windows backend", () => {
  const provider = usableWindowsAcl();

  assert.equal(provider.evaluate(requirement({ isolation: "required" })).satisfied, false);
  assert.equal(provider.evaluate(requirement({ isolation: "preferred" })).satisfied, true);
});

test("the runner invocation follows the stable argv contract", () => {
  const result = usableWindowsAcl().confine(["echo", "hi"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  assert.deepEqual(result.argv, [
    process.execPath,
    RUNNER,
    "--workspace",
    "/work",
    "--temp",
    tmpdir(),
    "--mode",
    "read-only",
    "--",
    "echo",
    "hi",
  ]);
});

test("the agentless flow passes no capability SID pair", () => {
  // Odessy's policy carries no session identity, which maps to the runner's
  // standalone flow: `--temp` names a root and the runner derives, grants, and
  // revokes the private child's own capability. Emitting the seam's
  // `--write-sid`/`--temp-write-sid` pair here would claim grants nobody
  // materialized.
  const result = usableWindowsAcl().confine(["echo"], {
    mode: "workspace-write",
    workspaceRoot: mkdtempSync(join(tmpdir(), "odessy-ws-")),
    deviceGrants: [],
  });

  assert.equal(result.argv.includes("--write-sid"), false);
  assert.equal(result.argv.includes("--temp-write-sid"), false);
  assert.deepEqual(result.argv.slice(-2), ["--", "echo"]);
});

test("a workspace-write temp root is checked for overlap", () => {
  // The workspace is created *inside* the ambient temp root, so this test fails
  // if the two arguments are ever swapped: the check would then ask whether the
  // temp root contains the workspace, which it does.
  const workspace = mkdtempSync(join(tmpdir(), "odessy-ws-"));

  assert.doesNotThrow(() =>
    usableWindowsAcl().confine(["echo"], {
      mode: "workspace-write",
      workspaceRoot: workspace,
      deviceGrants: [],
    }),
  );
});

test("a runner failure needs both the exit code and the signature", () => {
  const result = usableWindowsAcl().confine(["echo"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  const rule = result.runnerFailureRules[0];
  assert.ok(rule);
  assert.deepEqual(rule.allowedExitCodes, [127]);
  assert.deepEqual(rule.fatalSignatures, ["windows-acl-run: "]);

  // Both together: the runner failed before running the command.
  assert.notEqual(
    classifyRunnerFailure(127, "windows-acl-run: --workspace is not an existing directory\n", result.runnerFailureRules),
    undefined,
  );
  // The signature alone, on a status the runner never reserves: a confined
  // command that merely printed it ran successfully.
  assert.equal(classifyRunnerFailure(1, "windows-acl-run: decoy\n", result.runnerFailureRules), undefined);
  // The exit code alone: a confined command legitimately exiting 127.
  assert.equal(classifyRunnerFailure(127, "command not found\n", result.runnerFailureRules), undefined);
});

test("the Windows denial dialect carries all four spellings", () => {
  const result = usableWindowsAcl().confine(["echo"], {
    mode: "read-only",
    workspaceRoot: "/work",
    deviceGrants: [],
  });

  // Four layers report the same denial differently: PowerShell/.NET, cmd, and
  // Node's EACCES and EPERM.
  assert.deepEqual(result.denialSignatures, [
    "access is denied",
    "access to the path",
    "permission denied",
    "operation not permitted",
  ]);
});

// --------------------------------------------------------------------------
// The Windows probe is a functional verdict, not a presence check
// --------------------------------------------------------------------------

test("the Windows probe runs the runner in read-only mode", () => {
  const argv = windowsAclProbeArgv(RUNNER);

  // Read-only because it applies zero grants and mutates no DACL, so probing
  // cannot alter the machine while still exercising token creation and spawn.
  assert.deepEqual(argv, [
    RUNNER,
    "--workspace",
    tmpdir(),
    "--temp",
    tmpdir(),
    "--mode",
    "read-only",
    "--",
    "cmd",
    "/c",
    "exit",
    "0",
  ]);
});

test("the Windows probe verdict distinguishes present from usable", () => {
  const ok = interpretWindowsAclProbe({ status: 0, stdout: "", stderr: "" }, RUNNER);
  assert.equal(ok.available, true);
  assert.equal(ok.runnerEntry, RUNNER);

  // Present but unable to confine must report unavailable, or the failure would
  // surface on the user's own command instead of in the report.
  const refused = interpretWindowsAclProbe(
    {
      status: 127,
      stdout: "",
      stderr: "windows-acl-run: CreateRestrictedToken failed (Win32 5)\n",
    },
    RUNNER,
  );
  assert.equal(refused.available, false);
  assert.match(refused.detail, /CreateRestrictedToken/);

  // The runner's diagnostic is on the second line here; only the first is quoted,
  // so the report stays one line.
  const noisy = interpretWindowsAclProbe(
    { status: 127, stdout: "", stderr: "some warning\nwindows-acl-run: bad args\n" },
    RUNNER,
  );
  assert.equal(noisy.available, false);
  assert.match(noisy.detail, /some warning/);

  const timedOut = interpretWindowsAclProbe(
    { status: null, stdout: "", stderr: "ETIMEDOUT" },
    RUNNER,
  );
  assert.equal(timedOut.available, false);
  assert.match(timedOut.detail, /did not complete/);

  const silent = interpretWindowsAclProbe({ status: 3, stdout: "", stderr: "" }, RUNNER);
  assert.equal(silent.available, false);
  assert.match(silent.detail, /no diagnostic on stderr/);
});
