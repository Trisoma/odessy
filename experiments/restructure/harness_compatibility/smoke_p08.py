"""Pinned-runtime evidence for prompt 08: the launch states its confinement.

What this proves, against the pinned 0.2.1rc2 binary rather than against a fake:

1. The pin **composes** the sandbox overlay Odessy emits: ``sandbox-policy`` carries the
   plan's ``mode`` and ``workspaceRoot``, so the launch is not inheriting the pin's own
   ``DSH_PERMISSION_MODE ?? 'workspace-write'`` / ``process.cwd()`` default.
2. An overlay that tries to widen (``danger-full-access``) **loses** to Odessy's, because
   the sandbox overlay is emitted last — and Odessy refuses such an overlay by name before
   it ever reaches the runtime (both are checked; this script checks the composition).
3. A real ``SessionController`` on the pinned binary reports the same plan it launched with
   (``identity['sandbox']``) and hands the child ``DSH_PERMISSION_MODE``.
4. A ``required`` policy either launches with ``isolation=required`` or refuses with the
   dimension this host cannot enforce named. It never silently claims the boundary.
5. ``close()`` leaves no orphan process.

The mode asserted here is ``workspace-write`` with no hidden paths and network allowed, so
the expected plan has nothing unenforced on any host; the point is the *pinning*, not the
host's limitations.

    python smoke_p08.py --exe <pinned exe> --home <DSH_HOME> --work <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# HERE is .../odessy/experiments/restructure/harness_compatibility, so the repository root
# is two levels up from HERE's parent chain: restructure -> experiments -> odessy.
sys.path.insert(0, str(HERE.parents[2] / "src"))

import mock_provider  # noqa: E402

from odessy.application.session import SessionController  # noqa: E402
from odessy.config.sandbox import SandboxSettings  # noqa: E402
from odessy.runtime_backends import RuntimeUnavailable  # noqa: E402
from odessy.runtime_backends.harness.sandbox import (  # noqa: E402
    materialize_sandbox_patch,
    plan_for_session,
)

MODEL = "mock-model"
PROMPT = "hello"
PLATFORM = ("SystemRoot", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROCESSOR_ARCHITECTURE")

WIDENING_OVERLAY = """\
# An operator overlay that tries to widen the confinement. Odessy emits its own sandbox
# overlay after every other layer, so this must lose the composition.
- id: sandbox-policy
  config:
    mode: danger-full-access
    workspaceRoot: 'C:/'
"""


def _rows(dumped: str) -> dict[str, list[str]]:
    """The composed config blocks, keyed by row id (enough for the sandbox rows)."""

    rows: dict[str, list[str]] = {}
    current: str | None = None
    entry_indent: int | None = None
    config_indent: int | None = None
    for raw in dumped.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if stripped.startswith("- id: "):
            if entry_indent is None or indent <= entry_indent:
                entry_indent = indent
                current = stripped[len("- id: "):].strip()
                rows.setdefault(current, [])
                config_indent = None
            continue
        if current is None or (entry_indent is not None and indent <= entry_indent):
            continue
        if stripped.startswith("config:"):
            config_indent = indent
            continue
        if config_indent is not None and indent > config_indent:
            rows[current].append(stripped)
    return rows


def _compose(exe: str, env: dict[str, str], patches: list[Path]) -> dict[str, list[str]]:
    argv = [exe, "--profile", os.environ.get("ODESSY_HARNESS_PROFILE", "acp")]
    for patch in patches:
        argv += ["--patch", str(patch)]
    argv.append("--dump-config")
    dumped = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=180,
                            encoding="utf-8", errors="replace")
    if dumped.returncode != 0:
        raise AssertionError(f"--dump-config failed: {(dumped.stderr or '').strip()[:400]}")
    return _rows(dumped.stdout or "")


def _profile(port: int) -> dict:
    return {"name": "custom", "adapter": "cloud", "provider": "custom",
            "model": MODEL, "endpoint": f"http://127.0.0.1:{port}/v1"}


def _make_session(profile: dict) -> SessionController:
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": profile},
        selected_profile="custom",
        runtime_selection="harness",
    )


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return str(pid) in out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    for path in (work / "mock", workspace, home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    checks: list[tuple[str, bool, str]] = []
    server, port = mock_provider.serve(work / "mock")
    try:
        exe = str(Path(args.exe).resolve())
        for name in PLATFORM:
            if name in os.environ:
                os.environ.setdefault(name, os.environ[name])
        os.environ.update({
            "ODESSY_HARNESS_EXE": exe,
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
            "ODESSY_HARNESS_VERSION": "",
        })

        settings = SandboxSettings(hidden_paths=(), extra_writable=(), network_read_only="allow",
                                   network_workspace_write="allow")
        plan = plan_for_session(workspace=workspace, settings=settings, mode="workspace-write",
                                environ={})
        overlay = materialize_sandbox_patch(home, plan)
        widening = work / "widening.yml"
        widening.write_text(WIDENING_OVERLAY, encoding="utf-8")

        child_env = {name: os.environ[name] for name in PLATFORM if name in os.environ}
        child_env.update({"DSH_HOME": str(home)})

        # 1 + 2: composition, with a widening overlay applied *before* Odessy's.
        composed = _compose(exe, child_env, [widening, overlay])
        policy_rows = composed.get("sandbox-policy") or []
        # The YAML dumper may emit a long scalar as a folded block (``workspaceRoot: >-``
        # followed by an indented line), so the value is searched in the whole block rather
        # than in the first line that mentions the key.
        policy_text = " ".join(policy_rows)
        mode_row = next((row for row in policy_rows if row.startswith("mode:")), "")
        checks.append(("the pin composes Odessy's mode", plan.mode in mode_row,
                       f"{mode_row!r} (expected {plan.mode})"))
        normalised = policy_text.replace("/", "\\").lower()
        checks.append(("the pin composes Odessy's workspace root",
                       str(plan.workspace_root).lower() in normalised,
                       f"{policy_text[:200]!r} (expected {plan.workspace_root})"))
        checks.append(("a widening overlay applied earlier loses the composition",
                       "danger-full-access" not in mode_row, f"{mode_row!r}"))
        approval_rows = composed.get("approval") or []
        checks.append(("the approval policy is pinned to ask, not derived from the environment",
                       any("ask" in row for row in approval_rows), json.dumps(approval_rows)))

        # 3 + 5: a real session on the pinned binary.
        async def one_turn():
            session = _make_session(_profile(port))
            try:
                result = await asyncio.wait_for(session.run_turn(PROMPT), 120)
                sandbox = dict(session.runtime.identity.get("sandbox") or {})
                child = session.runtime.config.child_env()
                pid = session.runtime._process.pid if session.runtime._process else 0
                return result, sandbox, child, pid
            finally:
                await session.close()

        result, sandbox, child, pid = asyncio.run(one_turn())
        checks.append(("the session completed a turn", bool(result.final_text), result.final_text[:60]))
        checks.append(("the runtime reports the plan it launched with",
                       sandbox.get("mode") == plan.mode
                       and str(sandbox.get("workspace_root", "")).lower()
                       == str(plan.workspace_root).lower(),
                       json.dumps({k: sandbox.get(k) for k in ("mode", "workspace_root")})))
        checks.append(("the child is handed DSH_PERMISSION_MODE",
                       child.get("DSH_PERMISSION_MODE") == plan.mode,
                       f"{child.get('DSH_PERMISSION_MODE')!r}"))
        checks.append(("the session records what is not enforced",
                       isinstance(sandbox.get("unenforced"), list), json.dumps(sandbox.get("unenforced"))))
        checks.append(("close() left no orphan", pid == 0 or not _alive(pid), f"pid={pid}"))

        # 4: a required policy either launches honestly or refuses by name.
        os.environ["ODESSY_ISOLATION"] = "required"
        try:
            session = _make_session(_profile(port))
        except RuntimeUnavailable as refused:
            public = refused.public()
            detail = f"{public.get('reason')} / {public.get('detail')}"
            checks.append(("a required policy this host cannot meet refuses by name",
                           bool(public.get("detail") or "sandbox" in detail.lower()), detail[:200]))
        else:
            awaited = session.runtime.sandbox
            checks.append(("a required policy is either met or refused, never downgraded",
                           awaited.isolation == "required" and awaited.launchable,
                           f"isolation={awaited.isolation} unenforced={list(awaited.unenforced)}"))
            asyncio.run(session.close())
        finally:
            os.environ.pop("ODESSY_ISOLATION", None)
    finally:
        server.shutdown()

    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    print(json.dumps({"checks": len(checks), "failed": failed}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
