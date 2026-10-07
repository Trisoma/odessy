import assert from "node:assert/strict";
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
  type BwrapAvailability,
  type HostFacts,
} from "../src/execution/probe.js";
import { BwrapProvider } from "../src/execution/providers/bwrap.js";
import { LandlockProvider } from "../src/execution/providers/landlock.js";
import { SeatbeltProvider } from "../src/execution/providers/seatbelt.js";

const LAUNCHER = "/usr/bin/landlock-run";

interface FactsOptions {
  abi?: number | null;
  launcherEnforcement?: Enforcement | null;
  launcherPath?: string | null;
  bwrapAvailability?: BwrapAvailability;
  bwrapPath?: string | null;
  seatbeltAvailable?: boolean;
  seatbeltPath?: string | null;
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
