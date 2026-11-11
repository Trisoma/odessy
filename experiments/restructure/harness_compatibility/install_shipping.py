"""Drive the packaged Flutter setup bridge exactly as the desktop setup does.

The shipping installer extracts its payload and launches
``desktop/odessy_desktop.exe --setup --payload <payload>``. The desktop setup sends one
bounded JSON request per owned local helper to ``odessy.setup.bridge``. This driver performs
the same requests, so a packaged/versioned installation, repair and uninstall can be
qualified without a checkout on the path.

``--stage`` is the extracted payload. ``--python`` overrides the helper interpreter; after
installation the desktop uses the installed version's own interpreter with no PYTHONPATH,
which is what ``--python <root>/versions/<id>/Scripts/python.exe`` reproduces.

Usage:
    python install_shipping.py review    --stage <payload> --root <fresh-root>
    python install_shipping.py install   --stage <payload> --root <fresh-root>
    python install_shipping.py repair    --stage <payload> --root <installed-root>
    python install_shipping.py verify    --stage <payload> --root <installed-root> [--python ...]
    python install_shipping.py spec      --stage <payload> --root <installed-root> [--python ...]
    python install_shipping.py uninstall --stage <payload> --root <installed-root> [--python ...]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MODEL = {"provider": "ollama", "name": "fixture-model", "endpoint": "http://127.0.0.1:11434"}


def helper_python(stage: Path, override: str = "") -> tuple[str, bool]:
    if override:
        return override, False
    candidate = stage / "runtime" / ("python.exe" if os.name == "nt" else "bin/python3")
    if not candidate.is_file():
        raise SystemExit("payload interpreter is missing: " + str(candidate))
    return str(candidate), True


def bridge(stage: Path, request: dict, *, python: str = "", timeout: int = 3600) -> dict:
    """One setup request, run by the payload's private interpreter or the installed one."""
    interpreter, bundled = helper_python(stage, python)
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("ODESSY_")
    }
    if bundled:
        environment["PYTHONPATH"] = str(stage / "bootstrap")
        environment["ODESSY_SETUP_PAYLOAD"] = str(stage)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        # The bundled helper is reached through PYTHONPATH=bootstrap, exactly as the desktop
        # setup does; only an installed helper runs isolated (`-I`).
        [interpreter, *(["-I"] if not bundled else []), "-m", "odessy.setup.bridge"],
        input=json.dumps(request) + "\n", cwd=stage.parent, env=environment,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    try:
        reply = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SystemExit("bridge produced no JSON reply:\n" + completed.stdout[-2000:]
                         + completed.stderr[-2000:])
    if reply.get("status") != "ok":
        raise SystemExit("bridge refused: " + json.dumps(reply)[:2000]
                         + "\n" + completed.stderr[-2000:])
    return reply["result"]


def call(stage: Path, identifier: str, parameters: dict, *, python: str = "") -> dict:
    return bridge(stage, {"id": identifier, "operation": "manage", "parameters": parameters},
                  python=python)


def selection() -> dict:
    return {"role": "combined", "model": MODEL}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("review", "install", "repair", "register", "verify", "spec", "uninstall"))
    parser.add_argument("--stage", required=True, help="the extracted setup payload")
    parser.add_argument("--root", required=True)
    parser.add_argument("--python", default="", help="installed helper interpreter")
    args = parser.parse_args()
    root = str(Path(args.root).resolve())
    stage = Path(args.stage).resolve()

    if args.command == "review":
        plan = call(stage, "review-1", {
            "action": "package-review", "root": root, "payload": str(stage),
            "selection": selection()})
        print(json.dumps({key: plan.get(key) for key in (
            "schema_version", "root", "bundle", "components", "absent_components",
            "payload_root", "plan_id")}, indent=2))
    elif args.command == "install":
        plan = call(stage, "install-review", {
            "action": "package-review", "root": root, "payload": str(stage),
            "selection": selection()})
        installed = call(stage, "install-1", {
            "action": "package-install", "root": root, "payload": str(stage),
            "selection": selection(), "confirm": plan["plan_id"]})
        print(json.dumps(installed, indent=2))
    elif args.command == "repair":
        plan = call(stage, "repair-review", {
            "action": "package-repair-review", "root": root, "payload": str(stage)})
        print(json.dumps({key: plan.get(key) for key in ("plan_id", "components", "root")}, indent=2))
        repaired = call(stage, "repair-1", {
            "action": "package-repair", "root": root, "payload": str(stage),
            "confirm": plan["plan_id"]})
        print(json.dumps(repaired, indent=2))
    elif args.command == "register":
        registered = call(stage, "register-1", {
            "action": "package-register", "root": root, "payload": str(stage)}, python=args.python)
        print(json.dumps(registered, indent=2))
    elif args.command == "verify":
        print(json.dumps(call(stage, "verify-1", {
            "action": "package-verify", "root": root}, python=args.python), indent=2))
    elif args.command == "spec":
        print(json.dumps(call(stage, "spec-1", {
            "action": "installed-spec", "root": root}, python=args.python), indent=2))
    else:
        plan = call(stage, "uninstall-review", {
            "action": "package-uninstall-review", "root": root}, python=args.python)
        print(json.dumps({key: plan.get(key) for key in (
            "plan_id", "installation_id", "operation", "keep_models", "keep_config")}, indent=2))
        removed = call(stage, "uninstall-1", {
            "action": "package-uninstall", "root": root, "confirm": plan["plan_id"]},
            python=args.python)
        print(json.dumps(removed, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
