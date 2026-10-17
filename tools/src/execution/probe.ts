/**
 * Capability-driven host discovery.
 *
 * Odessy discovers what the host actually offers, and selects a provider that
 * satisfies the request. This deliberately differs from upstream, which selects
 * from a static per-platform table:
 *
 * ```text
 * linux  -> bwrap, then landlock
 * darwin -> seatbelt
 * win32  -> the ACL restricted-token runner
 * ```
 *
 * A table keyed by platform name cannot express that a binary is installed but
 * unusable, that a kernel option was compiled out, or that the Landlock ABI is
 * too old to deny a particular operation.
 *
 * Discovery is expensive: it stats, execs, and reads directories. It therefore
 * runs once per process, and every provider reads the cached result. The cache is
 * explicitly resettable so tests do not depend on ordering.
 *
 * Probe reports are always surfaced to the user. Capability-driven selection can
 * otherwise surprise, and an unexplained selection is indistinguishable from a
 * wrong one.
 */

import { spawnSync } from "node:child_process";
import {
  accessSync,
  constants,
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
} from "node:fs";
import { release, tmpdir } from "node:os";
import { join } from "node:path";

import type { DeviceCapability, Enforcement, ExecutionPolicy } from "./contract.js";
import { bwrapProfileArgs } from "./profiles.js";
import { RUNNER_ENTRY } from "./win32/runner-entry.js";

/** Matches upstream's `probeTimeoutMs` default. */
export const PROBE_TIMEOUT_MS = 5_000;

const SYSTEM_NAMES: Readonly<Record<string, string>> = {
  win32: "Windows",
  darwin: "Darwin",
  linux: "Linux",
};

/** The native launcher the Landlock backend needs. Odessy does not ship it. */
export const LAUNCHER_NAME = "landlock-run";

/** Printed by the confinement probe when its write attempt FAILED. */
export const PROBE_MARKER_READONLY = "ODESSY_PROBE_READONLY";

/** Printed when the write attempt SUCCEEDED, which proves no boundary exists. */
export const PROBE_MARKER_WRITABLE = "ODESSY_PROBE_WRITABLE";

// --------------------------------------------------------------------------
// bwrap: four states, not one
// --------------------------------------------------------------------------

/**
 * The distinct bwrap situations. Collapsing them into a single "bwrap available"
 * state is the silent failure this type exists to prevent.
 *
 * `unavailable_binary_unusable` is the one that most often masquerades as
 * available: the binary is installed, so a presence check says yes, while the
 * functional probe fails and nothing is actually confined.
 *
 * `available_setuid_root` has materially different security implications from
 * `available_userns`: a flaw in bwrap is then a local privilege escalation,
 * whereas the unprivileged path keeps a flaw inside the user's own boundary.
 */
export type BwrapAvailability =
  | "unavailable_no_binary"
  | "unavailable_binary_unusable"
  | "available_userns"
  | "available_setuid_root";

export interface LandlockFacts {
  /**
   * `null` when the syscall is absent or refuses.
   *
   * The ABI decides which access rights can be denied at all, so it is reported
   * as a number rather than reduced to a boolean.
   */
  readonly abi: number | null;
  readonly detail: string;
}

export interface UserNamespaceFacts {
  /** `null` means the key is absent, which differs from a key set to zero. */
  readonly unprivilegedUsernsClone: boolean | null;
  readonly maxUserNamespaces: number | null;
  readonly apparmorRestrictUnprivilegedUserns: boolean | null;
}

export interface BwrapFacts {
  readonly availability: BwrapAvailability;
  readonly path: string | null;
  readonly isSetuidRoot: boolean;
  readonly version: string | null;
  readonly detail: string;
}

export interface SeatbeltFacts {
  readonly available: boolean;
  readonly path: string | null;
  readonly detail: string;
}

/**
 * The Windows restricted-token backend's facts.
 *
 * `available` is a *functional* verdict, not a presence check: it is the result of
 * running the runner in read-only mode — zero grants, no DACL mutation — around
 * `cmd /c exit 0`. A runner that exists but cannot create its token reports
 * unavailable, which is the distinction that stops a broken backend from being
 * selected and then failing on the user's own command.
 */
export interface WindowsAclFacts {
  readonly available: boolean;
  readonly runnerEntry: string | null;
  readonly detail: string;
}

export interface LandlockLauncherFacts {
  readonly path: string | null;
  readonly enforcement: Enforcement | null;
  readonly detail: string;
}

export interface HostFacts {
  readonly platform: string;
  readonly system: string;
  readonly release: string;
  readonly machine: string;
  readonly landlock: LandlockFacts;
  readonly landlockLauncher: LandlockLauncherFacts;
  readonly bwrap: BwrapFacts;
  readonly seatbelt: SeatbeltFacts;
  readonly windowsAcl: WindowsAclFacts;
  readonly userNamespaces: UserNamespaceFacts;
  readonly deviceNodes: Readonly<Partial<Record<DeviceCapability, readonly string[]>>>;
}

// --------------------------------------------------------------------------
// Small helpers
// --------------------------------------------------------------------------

export interface ProbeOutput {
  readonly status: number | null;
  readonly stdout: string;
  readonly stderr: string;
}

function runProbe(command: string, args: readonly string[]): ProbeOutput {
  const result = spawnSync(command, args as string[], {
    encoding: "utf8",
    timeout: PROBE_TIMEOUT_MS,
  });
  return {
    status: result.status ?? null,
    stdout: result.stdout ?? "",
    stderr: result.stderr ? result.stderr : (result.error?.message ?? ""),
  };
}

/** Resolve a command on `PATH` without going through a shell. */
function which(command: string): string | undefined {
  const separator = process.platform === "win32" ? ";" : ":";
  const extensions =
    process.platform === "win32" ? (process.env.PATHEXT ?? ".EXE").split(";") : [""];

  for (const directory of (process.env.PATH ?? "").split(separator)) {
    if (!directory) {
      continue;
    }
    for (const extension of extensions) {
      const candidate = join(directory, command + extension);
      try {
        accessSync(candidate, constants.X_OK);
        return candidate;
      } catch {
        // Keep looking.
      }
    }
  }
  return undefined;
}

function readMaybe(path: string): string | null {
  try {
    return readFileSync(path, "utf8").trim();
  } catch {
    return null;
  }
}

// --------------------------------------------------------------------------
// Landlock
// --------------------------------------------------------------------------

/**
 * Query the kernel for the Landlock ABI version.
 *
 * **This cannot be done from Node.** The ABI is obtainable only by issuing
 * `landlock_create_ruleset`, and the launcher's `--probe` reports whether the
 * kernel enforces fully or partially but never *which* right is missing. Both
 * facts are needed: the first decides whether the backend works at all, the
 * second decides which limitations are real.
 *
 * Reporting a guessed ABI, or deriving it from the kernel release, would break
 * rule that `partial` must describe a *known* limitation. So the ABI is reported as
 * unknown and the Landlock provider stays unavailable on this side.
 *
 * **State of play.** The provider and its ABI-dependent limitation derivation are
 * complete and fully tested against an injected ABI; only this input is missing. So
 * production host discovery reports Landlock unavailable on every Linux host, and
 * the injection used by tests remains a test seam rather than an escape hatch.
 *
 * Making it live means binding libc's `syscall`. `koffi` cannot issue a bare
 * syscall, so the declaration must be fixed-arity —
 * `long syscall(long, void*, size_t, unsigned int)` — called as
 * `syscall(444, NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)`. That declaration must be
 * verified on both x86_64 and arm64 before being trusted: a wrong FFI signature
 * yields a wrong ABI, which yields a wrong limitation set that still reads as a
 * considered statement about the host.
 */
export function probeLandlock(): LandlockFacts {
  if (process.platform !== "linux") {
    return {
      abi: null,
      detail: `Landlock is Linux-only; host is ${process.platform}`,
    };
  }
  return {
    abi: null,
    detail:
      "the Landlock ABI is only obtainable through the landlock_create_ruleset syscall, which " +
      "Node cannot issue without a native FFI binding; reported as unknown rather than guessed",
  };
}

/**
 * Locate the launcher and ask it to prove it can enforce.
 *
 * `--probe` makes the launcher install a maximal ruleset on a short-lived process
 * and report whether the kernel enforced it. That is the honest signal; a version
 * check would not distinguish enforcement from mere support.
 */
export function probeLandlockLauncher(): LandlockLauncherFacts {
  const executable = which(LAUNCHER_NAME);
  if (executable === undefined) {
    return {
      path: null,
      enforcement: null,
      detail:
        `no ${LAUNCHER_NAME} launcher on PATH; Odessy does not ship the native launcher, ` +
        "so this backend is unavailable without one",
    };
  }

  const { status, stdout, stderr } = runProbe(executable, ["--probe"]);
  if (status === 0) {
    const reported = stdout.trim();
    return {
      path: executable,
      enforcement: reported.includes("partially enforced") ? "partial" : "full",
      detail: `${LAUNCHER_NAME} --probe reported ${reported || "success"}`,
    };
  }

  return {
    path: executable,
    enforcement: null,
    detail:
      `${executable} --probe refused with exit ${status}; this kernel does not enforce ` +
      `Landlock: ${stderr.trim() || "no diagnostic"}`,
  };
}

// --------------------------------------------------------------------------
// bwrap
// --------------------------------------------------------------------------

/** POSIX single-quote escaping: safe for any value except the empty string. */
function shQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

/**
 * The command the confinement probe runs inside the profile.
 *
 * It *reports whether a write succeeded*, rather than merely exiting. A probe
 * that runs a command and inspects only the exit status cannot distinguish "the
 * boundary held" from "bwrap never ran the command" — and bwrap failing to start
 * is exactly the common case this probe exists to catch.
 */
export function bwrapProbeScript(target: string): string {
  // The write runs in a subshell. `:` is a special built-in, and POSIX makes a
  // redirection error on one fatal to a non-interactive shell; dash (/bin/sh on
  // Debian and Ubuntu) exits there, so without the subshell a denied write printed
  // neither marker and read as "bwrap never ran the command".
  return (
    `if (: > ${shQuote(target)}) 2>/dev/null; then echo ${PROBE_MARKER_WRITABLE}; ` +
    `else echo ${PROBE_MARKER_READONLY}; fi`
  );
}

/**
 * The confinement probe's argv.
 *
 * It runs the same read-only profile the provider emits, wrapped around a command
 * that attempts a write the profile must deny. Upstream's probe ran a bare
 * `true`, which proves bwrap can assemble a profile but not that the profile
 * denies anything; `bwrap --version` proves even less.
 */
export function bwrapProbeArgv(executable: string, target: string): string[] {
  const policy: ExecutionPolicy = {
    mode: "read-only",
    workspaceRoot: "/",
    deviceGrants: [],
  };
  return [
    executable,
    ...bwrapProfileArgs(policy),
    "--",
    "/bin/sh",
    "-c",
    bwrapProbeScript(target),
  ];
}

/**
 * Map one confinement probe's output onto `(probeStatus, detail)`.
 *
 * Pure, so the three-way distinction is testable without a host that can run
 * bwrap. A `probeStatus` of `0` means the boundary was *demonstrated*.
 *
 * `residue` is whether the file the probe tried to create exists afterwards. It
 * is checked separately from the marker because the two failing independently is
 * exactly the case worth catching: a command that reports one thing and does
 * another.
 */
export function interpretBwrapProbe(
  stdout: string,
  stderr: string,
  status: number | null,
  residue: boolean,
): [number | null, string] {
  if (stdout.includes(PROBE_MARKER_READONLY) && !residue) {
    return [0, "the read-only profile denied a write, so the boundary is real"];
  }
  if (stdout.includes(PROBE_MARKER_WRITABLE) || residue) {
    return [
      1,
      "bwrap ran the probe command but did NOT deny the write, so no confinement " +
        "boundary exists on this host; refusing to report the backend as available",
    ];
  }
  return [
    null,
    `bwrap is installed but the probe never ran the command: ${stderr.trim() || `exit ${status}`}`,
  ];
}

/**
 * Map probe evidence to one of the four states.
 *
 * Pure, so the mapping is testable without a host that can actually run bwrap.
 */
export function classifyBwrapAvailability(
  path: string | null,
  probeStatus: number | null,
  isSetuidRoot: boolean,
): BwrapAvailability {
  if (path === null) {
    return "unavailable_no_binary";
  }
  if (probeStatus === 0) {
    return isSetuidRoot ? "available_setuid_root" : "available_userns";
  }
  return "unavailable_binary_unusable";
}

/** Discover bwrap's actual state on this host. */
export function probeBwrap(): BwrapFacts {
  if (process.platform !== "linux") {
    return {
      availability: "unavailable_no_binary",
      path: null,
      isSetuidRoot: false,
      version: null,
      detail: `bwrap is a Linux tool; host is ${process.platform}`,
    };
  }

  const executable = which("bwrap");
  if (executable === undefined) {
    return {
      availability: "unavailable_no_binary",
      path: null,
      isSetuidRoot: false,
      version: null,
      detail: "bwrap is not on PATH",
    };
  }

  let isSetuidRoot = false;
  try {
    isSetuidRoot = (statSync(executable).mode & 0o4000) !== 0;
  } catch {
    // Racy filesystem; leaving it false under-claims rather than over-claims.
  }

  const versionProbe = runProbe(executable, ["--version"]);
  const version = versionProbe.status === 0 ? versionProbe.stdout.trim() : null;

  const scratch = mkdtempSync(join(tmpdir(), "odessy-bwrap-probe-"));
  let probeStatus: number | null;
  let detail: string;
  try {
    const target = join(scratch, "write");
    const [command, ...args] = bwrapProbeArgv(executable, target);
    const { status, stdout, stderr } = runProbe(command, args);
    let residue = true;
    try {
      statSync(target);
    } catch {
      residue = false;
    }
    [probeStatus, detail] = interpretBwrapProbe(stdout, stderr, status, residue);
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }

  return {
    availability: classifyBwrapAvailability(executable, probeStatus, isSetuidRoot),
    path: executable,
    isSetuidRoot,
    version,
    detail,
  };
}

// --------------------------------------------------------------------------
// Seatbelt
// --------------------------------------------------------------------------

/** Discover macOS `sandbox-exec` availability. */
export function probeSeatbelt(): SeatbeltFacts {
  if (process.platform !== "darwin") {
    return {
      available: false,
      path: null,
      detail: `seatbelt is macOS-only; host is ${process.platform}`,
    };
  }

  const executable = which("sandbox-exec");
  if (executable === undefined) {
    return { available: false, path: null, detail: "sandbox-exec is not on PATH" };
  }

  // A permissive profile: this asks whether sandbox-exec runs at all, not
  // whether any particular policy is enforceable.
  const { status, stderr } = runProbe(executable, [
    "-p",
    "(version 1) (allow default)",
    "--",
    "true",
  ]);
  if (status === 0) {
    return { available: true, path: executable, detail: "functional probe passed" };
  }
  return {
    available: false,
    path: executable,
    detail: `sandbox-exec refused the profile: ${stderr.trim() || `exit ${status}`}`,
  };
}

// --------------------------------------------------------------------------
// User namespaces
// --------------------------------------------------------------------------

/**
 * Read the sysctls that govern unprivileged user namespaces.
 *
 * Recorded as explanation, not as the verdict. The functional bwrap probe is
 * authoritative, because sysctl values can be present, absent, or overridden by
 * an LSM policy in ways a page read cannot see.
 */
export function probeUserNamespaces(): UserNamespaceFacts {
  const clone = readMaybe("/proc/sys/kernel/unprivileged_userns_clone");
  const maximum = readMaybe("/proc/sys/user/max_user_namespaces");
  const apparmor = readMaybe("/proc/sys/kernel/apparmor_restrict_unprivileged_userns");

  return {
    unprivilegedUsernsClone: clone === null ? null : clone === "1",
    maxUserNamespaces:
      maximum === null || !/^\d+$/.test(maximum) ? null : Number.parseInt(maximum, 10),
    apparmorRestrictUnprivilegedUserns: apparmor === null ? null : apparmor === "1",
  };
}

// --------------------------------------------------------------------------
// Device nodes
// --------------------------------------------------------------------------

/**
 * Where each device capability shows up on a Linux host.
 *
 * PWM and ADC live in sysfs rather than `/dev`; that is recorded as it is rather
 * than forced into a device-node shape it does not have.
 *
 * These are not execution backends. They are the surfaces a *confined process*
 * could reach directly, which is why they are discovered here.
 */
const DEVICE_PATTERNS: Readonly<Record<DeviceCapability, readonly string[]>> = {
  gpio: ["/dev/gpiochip*"],
  pwm: ["/sys/class/pwm/pwmchip*"],
  adc: ["/sys/bus/iio/devices/iio:device*"],
  serial: ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/ttyTHS*"],
  spi: ["/dev/spidev*"],
  i2c: ["/dev/i2c-*"],
  can: ["/dev/can*"],
  usb: ["/dev/bus/usb/*/*"],
  camera: ["/dev/video*"],
};

/**
 * Match a pattern with `*` in its final segment against the filesystem.
 *
 * Deliberately minimal rather than a general glob: the patterns above are the
 * only shapes needed, and a bounded matcher cannot run away on a large tree.
 */
function matchPattern(pattern: string): string[] {
  const segments = pattern.split("/").filter((segment) => segment.length > 0);
  const rooted = pattern.startsWith("/");

  const walk = (base: string, index: number): string[] => {
    if (index >= segments.length) {
      return [base];
    }
    const segment = segments[index] as string;
    if (!segment.includes("*")) {
      const candidate = base === "" ? `/${segment}` : join(base, segment);
      try {
        statSync(candidate);
      } catch {
        return [];
      }
      return walk(candidate, index + 1);
    }

    const prefix = segment.slice(0, segment.indexOf("*"));
    const suffix = segment.slice(segment.indexOf("*") + 1);
    let entries: string[];
    try {
      entries = readdirSync(base === "" ? "/" : base);
    } catch {
      return [];
    }
    return entries
      .filter((entry) => entry.startsWith(prefix) && entry.endsWith(suffix))
      .flatMap((entry) => walk(join(base, entry), index + 1));
  };

  return walk(rooted ? "" : ".", 0);
}

/** Discover which device nodes exist. */
export function probeDeviceNodes(): Partial<Record<DeviceCapability, readonly string[]>> {
  const found: Partial<Record<DeviceCapability, string[]>> = {};
  for (const [capability, patterns] of Object.entries(DEVICE_PATTERNS)) {
    const matches = patterns.flatMap(matchPattern);
    if (matches.length > 0) {
      found[capability as DeviceCapability] = [...new Set(matches)].sort();
    }
  }
  return found;
}

// --------------------------------------------------------------------------
// Windows: the restricted-token ACL runner
// --------------------------------------------------------------------------

/**
 * The runner invocation the read-only probe uses.
 *
 * Read-only is chosen deliberately: it applies zero capability grants and mutates
 * no DACL, so probing cannot alter the machine. What it does exercise is the part
 * that fails on an otherwise-present runner — opening the process token,
 * deriving a restricted token from it, and spawning a child under that token.
 *
 * `tmpdir()` serves as both roots. Read-only never applies the workspace/temp
 * disjointness rule, which exists to stop two *write* capabilities merging, and
 * read-only carries none.
 */
export function windowsAclProbeArgv(runnerEntry: string): string[] {
  return [
    runnerEntry,
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
  ];
}

/**
 * Turn a probe execution into facts.
 *
 * Pure, so the verdict is testable without spawning the runner. The distinction
 * that matters is between *present* and *usable*: a runner that exists but cannot
 * create its token must report unavailable here, because a presence check would
 * call it available and its failure would then surface on the user's own command
 * rather than in the report.
 */
export function interpretWindowsAclProbe(
  output: ProbeOutput,
  runnerEntry: string,
): WindowsAclFacts {
  if (output.status === 0) {
    return {
      available: true,
      runnerEntry,
      detail: "restricted token created and a child spawned under it (read-only probe)",
    };
  }

  const diagnostic = output.stderr.trim().split("\n")[0] ?? "";
  if (output.status === null) {
    return {
      available: false,
      runnerEntry,
      detail: `probe did not complete: ${diagnostic === "" ? "unknown error" : diagnostic}`,
    };
  }
  return {
    available: false,
    runnerEntry,
    detail:
      `probe exited ${output.status}: ` +
      `${diagnostic === "" ? "no diagnostic on stderr" : diagnostic}`,
  };
}

/**
 * Whether this host can confine through the runner.
 *
 * The runner is a *script*: `runner.ts` calls `main()` at module scope, so
 * importing it would confine and spawn a process as a side effect of a module
 * load. It is therefore referenced by path and never imported — see
 * `win32/runner-entry.ts`.
 */
export function probeWindowsAcl(): WindowsAclFacts {
  if (process.platform !== "win32") {
    return {
      available: false,
      runnerEntry: null,
      detail: `the ACL runner is Windows-only; host is ${process.platform}`,
    };
  }
  if (!existsSync(RUNNER_ENTRY)) {
    return {
      available: false,
      runnerEntry: null,
      detail: `runner entry not found: ${RUNNER_ENTRY}`,
    };
  }
  return interpretWindowsAclProbe(
    runProbe(process.execPath, windowsAclProbeArgv(RUNNER_ENTRY)),
    RUNNER_ENTRY,
  );
}

// --------------------------------------------------------------------------
// Composition
// --------------------------------------------------------------------------

function discover(): HostFacts {
  return {
    platform: process.platform,
    system: SYSTEM_NAMES[process.platform] ?? process.platform,
    release: release(),
    machine: process.arch,
    landlock: probeLandlock(),
    landlockLauncher: probeLandlockLauncher(),
    bwrap: probeBwrap(),
    seatbelt: probeSeatbelt(),
    windowsAcl: probeWindowsAcl(),
    userNamespaces: probeUserNamespaces(),
    deviceNodes: probeDeviceNodes(),
  };
}

let cached: HostFacts | undefined;

/**
 * The cached discovery pass.
 *
 * Cached because discovery execs processes and reads directories. Every provider
 * reads this one result, so selection does not re-probe per candidate, and a
 * per-request `evaluate` stays a pure function of cached facts.
 */
export function discoverHostFacts(): HostFacts {
  cached ??= discover();
  return cached;
}

/** Drop the cache. Tests use this; production never needs to. */
export function resetHostFacts(): void {
  cached = undefined;
}

// --------------------------------------------------------------------------
// Reporting
// --------------------------------------------------------------------------

/**
 * Render the discovery pass for a human.
 *
 * The report is part of the contract with the user: selection is
 * capability-driven, so the capabilities have to be visible.
 */
export function renderHostFacts(facts: HostFacts): string {
  const lines = [
    "host",
    `  platform        ${facts.system} ${facts.release} (${facts.machine})`,
    `  landlock        ${facts.landlock.detail}`,
    `  launcher        ${facts.landlockLauncher.detail}`,
    `  bwrap           ${facts.bwrap.availability}`,
    `                  ${facts.bwrap.detail}`,
  ];

  if (facts.bwrap.path !== null) {
    const setuid = facts.bwrap.isSetuidRoot ? " (setuid root)" : "";
    const version = facts.bwrap.version ? ` ${facts.bwrap.version}` : "";
    lines.push(`                  ${facts.bwrap.path}${setuid}${version}`);
  }

  lines.push(`  seatbelt        ${facts.seatbelt.detail}`);
  lines.push(`  windows acl     ${facts.windowsAcl.detail}`);

  const namespaces = facts.userNamespaces;
  lines.push(
    "  user namespaces " +
      `unprivileged_userns_clone=${namespaces.unprivilegedUsernsClone} ` +
      `max_user_namespaces=${namespaces.maxUserNamespaces} ` +
      `apparmor_restrict=${namespaces.apparmorRestrictUnprivilegedUserns}`,
  );

  const entries = Object.entries(facts.deviceNodes);
  if (entries.length > 0) {
    lines.push("  device nodes");
    for (const [capability, paths] of entries.sort(([a], [b]) => a.localeCompare(b))) {
      lines.push(`    ${capability.padEnd(8)} ${(paths ?? []).join(", ")}`);
    }
  } else {
    lines.push("  device nodes    none discovered");
  }

  return lines.join("\n");
}
