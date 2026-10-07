"""Capability-driven host discovery.

Odessy discovers what the host actually offers, and selects a provider that
satisfies the request. This deliberately differs from upstream, which selects
from a static per-platform table:

```text
linux  -> bwrap, then landlock
darwin -> seatbelt
win32  -> the ACL restricted-token runner
```

A table keyed by platform name cannot express that a binary is installed but
unusable, that a kernel option was compiled out, or that the Landlock ABI is too
old to deny a particular operation. Those are exactly the cases the request has
to be judged against, so they are discovered rather than assumed.

Discovery is expensive: it stats, execs, and issues a syscall. It therefore runs
once per process, and every provider reads the cached result. The cache is
explicitly resettable so tests do not depend on ordering.

Probe reports are always surfaced to the user. Capability-driven selection can
otherwise surprise, and an unexplained selection is indistinguishable from a
wrong one.
"""

from __future__ import annotations

import ctypes
import errno
import glob
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from odessy.execution.contract import DeviceCapability, Enforcement, ExecutionPolicy
from odessy.execution.profiles import bwrap_profile_args

__all__ = [
    "PROBE_MARKER_READONLY",
    "PROBE_MARKER_WRITABLE",
    "BwrapAvailability",
    "BwrapFacts",
    "HostFacts",
    "LandlockFacts",
    "LandlockLauncherFacts",
    "SeatbeltFacts",
    "UserNamespaceFacts",
    "bwrap_probe_argv",
    "bwrap_probe_script",
    "classify_bwrap_availability",
    "discover_host_facts",
    "interpret_bwrap_probe",
    "probe_landlock_launcher",
    "render_host_facts",
    "reset_host_facts",
]

#: Matches upstream's `probeTimeoutMs` default.
PROBE_TIMEOUT_SECONDS = 5.0

#: ``landlock_create_ruleset``. These three syscalls occupy a fixed, architecture
#: independent range (444-446), which is why no per-architecture table is needed.
_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_CREATE_RULESET_VERSION = 1 << 0


# --------------------------------------------------------------------------
# bwrap: four states, not one
# --------------------------------------------------------------------------

#: The distinct bwrap situations. Collapsing them into a single "bwrap available"
#: state is the silent failure this type exists to prevent.
#:
#: ``available_userns``
#:     The binary works and creates its namespaces unprivileged.
#: ``available_setuid_root``
#:     The binary works because it is setuid-root. Different security
#:     implications: a flaw in bwrap is then a local privilege escalation,
#:     whereas the unprivileged path keeps a flaw inside the user's own boundary.
#: ``unavailable_binary_unusable``
#:     The binary is installed but the functional probe fails, typically because
#:     unprivileged user namespaces are disabled by sysctl or by an LSM policy.
#:     This is the state that most often masquerades as "available".
#: ``unavailable_no_binary``
#:     Nothing to run.
BwrapAvailability = Literal[
    "unavailable_no_binary",
    "unavailable_binary_unusable",
    "available_userns",
    "available_setuid_root",
]


@dataclass(frozen=True, slots=True)
class LandlockFacts:
    """What the running kernel reports about Landlock.

    ``abi`` is ``None`` when the syscall is absent or refuses, which is the
    honest answer for a kernel built without ``CONFIG_SECURITY_LANDLOCK``. The
    ABI value decides which access rights can be denied at all, so it is reported
    rather than reduced to a boolean.
    """

    abi: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class UserNamespaceFacts:
    """The sysctls that govern unprivileged user namespaces.

    ``None`` means the key is absent on this kernel, which is different from a
    key present and set to zero.
    """

    unprivileged_userns_clone: bool | None
    max_user_namespaces: int | None
    apparmor_restrict_unprivileged_userns: bool | None


@dataclass(frozen=True, slots=True)
class BwrapFacts:
    """One bwrap probe's result, with the evidence behind the verdict."""

    availability: BwrapAvailability
    path: str | None
    is_setuid_root: bool
    version: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class SeatbeltFacts:
    """macOS ``sandbox-exec`` availability."""

    available: bool
    path: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class HostFacts:
    """Everything one discovery pass learned about this host.

    Immutable and cached per process. Providers derive their own probe reports
    from these facts rather than re-running the discovery themselves.
    """

    platform: str
    system: str
    release: str
    machine: str
    landlock: LandlockFacts
    landlock_launcher: LandlockLauncherFacts
    bwrap: BwrapFacts
    seatbelt: SeatbeltFacts
    user_namespaces: UserNamespaceFacts
    device_nodes: Mapping[DeviceCapability, tuple[str, ...]]


# --------------------------------------------------------------------------
# Landlock
# --------------------------------------------------------------------------


def probe_landlock() -> LandlockFacts:
    """Query the kernel for the Landlock ABI version.

    Landlock is asked directly rather than inferred from configuration, because
    an ABI value is the only thing that says which operations can be denied. A
    kernel without the syscall answers ``ENOSYS``, which is reported as an absent
    ABI with the reason attached.
    """

    if sys.platform != "linux":
        return LandlockFacts(abi=None, detail=f"Landlock is Linux-only; host is {sys.platform}")

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.argtypes = [
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint,
        ]
        libc.syscall.restype = ctypes.c_long
        result = libc.syscall(_LANDLOCK_CREATE_RULESET, None, 0, _LANDLOCK_CREATE_RULESET_VERSION)
    except (OSError, AttributeError, TypeError) as error:  # pragma: no cover - platform dependent
        return LandlockFacts(abi=None, detail=f"could not query the Landlock syscall: {error}")

    if result > 0:
        return LandlockFacts(abi=int(result), detail=f"Landlock ABI {int(result)}")

    number = ctypes.get_errno()
    symbol = errno.errorcode.get(number, str(number))
    return LandlockFacts(
        abi=None,
        detail=f"landlock_create_ruleset refused with {symbol} ({os.strerror(number)})",
    )


#: The native launcher the Landlock backend needs. Odessy does not ship it.
LAUNCHER_NAME = "landlock-run"


@dataclass(frozen=True, slots=True)
class LandlockLauncherFacts:
    """The native ``landlock-run`` launcher, when this host has one.

    The ABI alone is not enough to promise confinement: a kernel can expose the
    syscalls and still refuse enforcement, and the ruleset still has to be
    installed by a runner that then execs the target. So when the launcher is
    present it is asked to prove itself, and when it is absent that is stated
    plainly rather than left as a gap the user has to infer.
    """

    path: str | None
    enforcement: Enforcement | None
    detail: str


def probe_landlock_launcher() -> LandlockLauncherFacts:
    """Locate the launcher and ask it to prove it can enforce.

    ``--probe`` makes the launcher install a maximal ruleset on a short-lived
    process and report whether the kernel enforced it. That is the honest
    signal; a version check would not distinguish enforcement from mere support.
    """

    executable = shutil.which(LAUNCHER_NAME)
    if executable is None:
        return LandlockLauncherFacts(
            path=None,
            enforcement=None,
            detail=(
                f"no {LAUNCHER_NAME} launcher on PATH; Odessy does not ship the native "
                "launcher, so this backend is unavailable without one"
            ),
        )

    exit_code, stdout, stderr = _run_probe([executable, "--probe"])
    if exit_code == 0:
        reported = stdout.strip()
        enforcement: Enforcement = "partial" if "partially enforced" in reported else "full"
        return LandlockLauncherFacts(
            path=executable,
            enforcement=enforcement,
            detail=f"{LAUNCHER_NAME} --probe reported {reported or enforcement}",
        )

    return LandlockLauncherFacts(
        path=executable,
        enforcement=None,
        detail=(
            f"{executable} --probe refused with exit {exit_code}; this kernel does not "
            f"enforce Landlock: {stderr.strip() or 'no diagnostic'}"
        ),
    )


# --------------------------------------------------------------------------
# bwrap
# --------------------------------------------------------------------------

#: Printed by the confinement probe when its write attempt FAILED. This is the
#: outcome that proves a boundary exists.
PROBE_MARKER_READONLY = "ODESSY_PROBE_READONLY"

#: Printed when the write attempt SUCCEEDED, which proves no boundary exists.
PROBE_MARKER_WRITABLE = "ODESSY_PROBE_WRITABLE"


def bwrap_probe_script(target: str) -> str:
    """The command the confinement probe runs inside the profile.

    It *reports whether a write succeeded*, rather than merely exiting. A probe
    that runs a command and inspects only the exit status cannot distinguish
    "the boundary held" from "bwrap never ran the command" — and bwrap failing
    to start is exactly the common case this probe exists to catch.
    """

    quoted = shlex.quote(target)
    return (
        f"if : > {quoted} 2>/dev/null; then "
        f"echo {PROBE_MARKER_WRITABLE}; "
        f"else echo {PROBE_MARKER_READONLY}; fi"
    )


def bwrap_probe_argv(executable: str, target: str) -> list[str]:
    """The confinement probe's argv.

    It runs the same read-only profile the provider emits, wrapped around a
    command that attempts a write the profile must deny. Upstream's probe ran a
    bare ``true``, which proves bwrap can assemble a profile but not that the
    profile denies anything; ``bwrap --version`` proves even less. Attempting a
    prohibited write is the only signal that separates an enforcement boundary
    from a successful spawn.

    ``/`` is passed as the workspace root so the whole host root is bound
    read-only, which makes the scratch target read-only too.
    """

    policy = ExecutionPolicy(mode="read-only", workspace_root="/")
    return [
        executable,
        *bwrap_profile_args(policy),
        "--",
        "/bin/sh",
        "-c",
        bwrap_probe_script(target),
    ]


def classify_bwrap_availability(
    path: str | None,
    *,
    is_setuid_root: bool,
    probe_exit_code: int | None,
) -> BwrapAvailability:
    """Map probe evidence to one of the four states.

    Pure, so the mapping is testable without a host that can actually run bwrap.
    """

    if path is None:
        return "unavailable_no_binary"
    if probe_exit_code == 0:
        return "available_setuid_root" if is_setuid_root else "available_userns"
    return "unavailable_binary_unusable"


def _run_probe(argv: list[str]) -> tuple[int | None, str, str]:
    """Run one functional probe. Returns ``(exit_code, stdout, stderr)``."""

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, "", str(error)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def interpret_bwrap_probe(
    stdout: str,
    stderr: str,
    exit_code: int | None,
    residue: bool,
) -> tuple[int | None, str]:
    """Map one confinement probe's output onto ``(probe_exit_code, detail)``.

    Pure, so the three-way distinction is testable without a host that can run
    bwrap. A ``probe_exit_code`` of ``0`` means the boundary was *demonstrated*;
    anything else means it was not, and the detail says which way.

    ``residue`` is whether the file the probe tried to create exists afterwards.
    It is checked separately from the marker because the two failing
    independently is exactly the case worth catching: a command that reports one
    thing and does another.
    """

    if PROBE_MARKER_READONLY in stdout and not residue:
        return 0, "the read-only profile denied a write, so the boundary is real"

    if PROBE_MARKER_WRITABLE in stdout or residue:
        return (
            1,
            "bwrap ran the probe command but did NOT deny the write, so no confinement "
            "boundary exists on this host; refusing to report the backend as available",
        )

    return (
        None,
        "bwrap is installed but the probe never ran the command: "
        f"{stderr.strip() or f'exit {exit_code}'}",
    )


def probe_bwrap() -> BwrapFacts:
    """Discover bwrap's actual state on this host."""

    if sys.platform != "linux":
        return BwrapFacts(
            availability="unavailable_no_binary",
            path=None,
            is_setuid_root=False,
            version=None,
            detail=f"bwrap is a Linux tool; host is {sys.platform}",
        )

    executable = shutil.which("bwrap")
    if executable is None:
        return BwrapFacts(
            availability="unavailable_no_binary",
            path=None,
            is_setuid_root=False,
            version=None,
            detail="bwrap is not on PATH",
        )

    is_setuid_root = False
    try:
        is_setuid_root = bool(os.stat(executable).st_mode & stat.S_ISUID)
    except OSError:  # pragma: no cover - racy filesystem
        pass

    version_code, version_stdout, version_stderr = _run_probe([executable, "--version"])
    version = (version_stdout.strip() or version_stderr.strip()) if version_code == 0 else None

    scratch = tempfile.mkdtemp(prefix="odessy-bwrap-probe-")
    try:
        target = os.path.join(scratch, "write")
        exit_code, stdout, stderr = _run_probe(bwrap_probe_argv(executable, target))
        residue = os.path.exists(target)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    probe_exit_code, detail = interpret_bwrap_probe(stdout, stderr, exit_code, residue)

    availability = classify_bwrap_availability(
        executable, is_setuid_root=is_setuid_root, probe_exit_code=probe_exit_code
    )

    return BwrapFacts(
        availability=availability,
        path=executable,
        is_setuid_root=is_setuid_root,
        version=version,
        detail=detail,
    )


# --------------------------------------------------------------------------
# Seatbelt
# --------------------------------------------------------------------------


def probe_seatbelt() -> SeatbeltFacts:
    """Discover macOS ``sandbox-exec`` availability."""

    if sys.platform != "darwin":
        return SeatbeltFacts(
            available=False, path=None, detail=f"seatbelt is macOS-only; host is {sys.platform}"
        )

    executable = shutil.which("sandbox-exec")
    if executable is None:
        return SeatbeltFacts(available=False, path=None, detail="sandbox-exec is not on PATH")

    # A permissive profile: this asks whether sandbox-exec runs at all, not
    # whether any particular policy is enforceable.
    exit_code, _stdout, stderr = _run_probe(
        [executable, "-p", "(version 1) (allow default)", "--", "true"]
    )
    if exit_code == 0:
        return SeatbeltFacts(available=True, path=executable, detail="functional probe passed")
    return SeatbeltFacts(
        available=False,
        path=executable,
        detail=f"sandbox-exec refused the profile: {stderr.strip() or f'exit {exit_code}'}",
    )


# --------------------------------------------------------------------------
# User namespaces
# --------------------------------------------------------------------------


def _read_sysctl(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def probe_user_namespaces() -> UserNamespaceFacts:
    """Read the sysctls that govern unprivileged user namespaces.

    These are recorded as explanation, not as the verdict. The functional bwrap
    probe is authoritative, because sysctl values can be present, absent, or
    overridden by an LSM policy in ways a page read cannot see.
    """

    clone = _read_sysctl("/proc/sys/kernel/unprivileged_userns_clone")
    maximum = _read_sysctl("/proc/sys/user/max_user_namespaces")
    apparmor = _read_sysctl("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")

    return UserNamespaceFacts(
        unprivileged_userns_clone=None if clone is None else clone == "1",
        max_user_namespaces=None if maximum is None else int(maximum) if maximum.isdigit() else None,
        apparmor_restrict_unprivileged_userns=None if apparmor is None else apparmor == "1",
    )


# --------------------------------------------------------------------------
# Device nodes
# --------------------------------------------------------------------------

#: Where each device capability shows up on a Linux host. PWM and ADC live in
#: sysfs rather than ``/dev``; that is recorded as it is rather than forced into
#: a device-node shape it does not have.
_DEVICE_GLOBS: Mapping[DeviceCapability, tuple[str, ...]] = {
    "gpio": ("/dev/gpiochip*",),
    "pwm": ("/sys/class/pwm/pwmchip*",),
    "adc": ("/sys/bus/iio/devices/iio:device*",),
    "serial": ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/ttyTHS*"),
    "spi": ("/dev/spidev*",),
    "i2c": ("/dev/i2c-*",),
    "can": ("/dev/can*",),
    "usb": ("/dev/bus/usb/*/*",),
    "camera": ("/dev/video*",),
}


def probe_device_nodes() -> Mapping[DeviceCapability, tuple[str, ...]]:
    """Discover which device nodes exist.

    These are not execution backends. They are the surfaces a *confined process*
    could reach directly, which is why they are discovered here: the isolation
    layer denies them by default, and the authority layer gates them explicitly.
    """

    found: dict[DeviceCapability, tuple[str, ...]] = {}
    for capability, patterns in _DEVICE_GLOBS.items():
        matches: list[str] = []
        for pattern in patterns:
            matches.extend(glob.glob(pattern))
        if matches:
            found[capability] = tuple(sorted(dict.fromkeys(matches)))
    return found


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def _discover() -> HostFacts:
    return HostFacts(
        platform=sys.platform,
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
        landlock=probe_landlock(),
        landlock_launcher=probe_landlock_launcher(),
        bwrap=probe_bwrap(),
        seatbelt=probe_seatbelt(),
        user_namespaces=probe_user_namespaces(),
        device_nodes=probe_device_nodes(),
    )


@lru_cache(maxsize=1)
def discover_host_facts() -> HostFacts:
    """The cached discovery pass.

    Cached because discovery execs processes and issues a syscall. Every provider
    reads this one result, so selection does not re-probe per candidate, and a
    per-request ``evaluate`` stays a pure function of cached facts.
    """

    return _discover()


def reset_host_facts() -> None:
    """Drop the cache. Tests use this; production never needs to."""

    discover_host_facts.cache_clear()


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def render_host_facts(facts: HostFacts) -> str:
    """Render the discovery pass for a human.

    The report is part of the contract with the user: selection is
    capability-driven, so the capabilities have to be visible.
    """

    lines = [
        "host",
        f"  platform        {facts.system} {facts.release} ({facts.machine})",
        f"  landlock        {facts.landlock.detail}",
        f"  launcher        {facts.landlock_launcher.detail}",
        f"  bwrap           {facts.bwrap.availability}",
        f"                  {facts.bwrap.detail}",
    ]
    if facts.bwrap.path is not None:
        lines.append(
            f"                  {facts.bwrap.path}"
            f"{' (setuid root)' if facts.bwrap.is_setuid_root else ''}"
            f"{f' {facts.bwrap.version}' if facts.bwrap.version else ''}"
        )
    lines.append(f"  seatbelt        {facts.seatbelt.detail}")

    namespaces = facts.user_namespaces
    lines.append(
        "  user namespaces "
        f"unprivileged_userns_clone={namespaces.unprivileged_userns_clone}"
        f" max_user_namespaces={namespaces.max_user_namespaces}"
        f" apparmor_restrict={namespaces.apparmor_restrict_unprivileged_userns}"
    )

    if facts.device_nodes:
        lines.append("  device nodes")
        for capability, paths in sorted(facts.device_nodes.items()):
            lines.append(f"    {capability:<8} {', '.join(paths)}")
    else:
        lines.append("  device nodes    none discovered")

    return "\n".join(lines)
