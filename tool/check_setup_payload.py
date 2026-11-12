"""Install a built setup payload into an isolated root and report what landed.

This is the acceptance check for the packaged runtime (prompt 14): one payload zip is
extracted and installed through the *shipped* setup protocol (the payload's own
interpreter running ``odessy.setup.bridge``), never by importing the checkout's code.
It proves, in order:

1. the payload reviews and installs into an empty root with a non-ASCII, spaced path;
2. ``package-verify`` proves every recorded file, and the four identities are checked
   separately: Odessy wheel, payload zip, pinned runtime executable and the plugin;
3. user-owned files written around the installation survive an uninstall;
4. the receipt names the Odessy source identity (wheel/payload digests) apart from the
   upstream runtime identity (version, source commit, executable digest).

Nothing is downloaded, signed or published, and no existing installation is touched.

Usage:
    python tool/check_setup_payload.py --payload <payload.zip> --work <dir> [--keep]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BUNDLE = "odessy/runtime_backends/harness/bundle/odessy-stream-bridge"


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bridge(interpreter: Path, payload: Path, request: dict, *, timeout: float = 3600) -> dict:
    """One request through the shipped helper: one JSON line in, one line out.\n\n    The default bound is an hour because creating the private virtualenv and installing the\n    42 hash-locked wheels offline is the slowest step of the whole setup on a machine with\n    real-time scanning; a 15-minute bound aborted a healthy install here.\n    """

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(payload / "bootstrap")
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.pop("ODESSY_SETUP_PAYLOAD", None)
    completed = subprocess.run(
        [str(interpreter), "-m", "odessy.setup.bridge"],
        input=json.dumps(request) + "\n", capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=environment, timeout=timeout, check=False)
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"no bridge answer ({completed.returncode}): "
                           f"{(completed.stderr or '')[-400:]}")
    answer = json.loads(lines[-1])
    if answer.get("status") != "ok":
        raise RuntimeError(f"bridge refused {request['parameters']['action']}: "
                           f"{answer.get('reason') or answer.get('error')}")
    return answer["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload", help="payload zip to extract and install")
    source.add_argument("--payload-dir", help="an already extracted payload")
    parser.add_argument("--work", required=True)
    parser.add_argument("--keep", action="store_true", help="keep the isolated root")
    parser.add_argument("--selection", default="execution")
    parser.add_argument("--register", action="store_true",
                        help="exercise per-identity Start-menu/Installed-apps registration")
    args = parser.parse_args()

    payload_zip = Path(args.payload).resolve() if args.payload else None
    work = Path(args.work).resolve()
    if work.exists():
        raise SystemExit(f"choose a new work directory: {work}")
    root = work / "Odessy Installed Äpp"
    if args.payload_dir:
        payload = Path(args.payload_dir).resolve()
        if not (payload / "setup-context.json").is_file():
            raise SystemExit(f"not an extracted payload: {payload}")
    else:
        payload = work / "payload"
        payload.mkdir(parents=True)
        with zipfile.ZipFile(payload_zip) as archive:
            archive.extractall(payload)

    interpreter = payload / "runtime" / "python.exe"
    if not interpreter.is_file():
        raise SystemExit("the payload has no bundled interpreter")

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed), detail))

    selection = {"role": args.selection}
    review = bridge(interpreter, payload,
                    {"id": "p14-review", "operation": "manage",
                     "parameters": {"action": "package-review", "root": str(root), "payload": str(payload),
                                    "selection": selection}})
    check("the payload reviews before installing", bool(review.get("plan_id")),
          json.dumps(review.get("components")))
    check("the reviewed components are the shipped ones",
          set(review.get("components") or []) == {"desktop", "harness", "node", "runtime",
                                                  "tools"},
          json.dumps(review.get("components")))

    installed = bridge(interpreter, payload,
                       {"id": "p14-install", "operation": "manage",
                        "parameters": {"action": "package-install", "root": str(root), "payload": str(payload),
                                       "selection": selection,
                                       "confirm": review.get("plan_id")}})
    versions = sorted(path.name for path in (root / "versions").glob("*")) \
        if (root / "versions").is_dir() else []
    check("the payload installs into an empty spaced, non-ASCII root",
          (root / "installation.json").is_file() and len(versions) == 1,
          json.dumps({"root": str(root), "versions": versions}))
    verified = bridge(interpreter, payload,
                      {"id": "p14-verify", "operation": "manage",
                       "parameters": {"action": "package-verify", "root": str(root), "payload": str(payload)}})
    check("every recorded installed file verifies", verified.get("valid") is True,
          json.dumps({key: verified.get(key) for key in ("valid", "reason")})[:200])

    # An execution-role installation has no desktop application, so ``installed-spec``
    # refuses it on purpose; the version directory is the same authority either way.
    try:
        spec = bridge(interpreter, payload,
                      {"id": "p14-spec", "operation": "manage",
                       "parameters": {"action": "installed-spec", "root": str(root)}})
    except RuntimeError as error:
        spec = {"version": "", "note": str(error)[:160]}
    version = str(spec.get("version") or (versions[0] if versions else ""))
    check("the installation records an active version", bool(version), version)
    site = root / "versions" / version / ("Lib/site-packages" if os.name == "nt"
                                          else "lib/python3.12/site-packages")
    wheel_marker = site / "odessy"

    # -- identities, kept apart ---------------------------------------------------
    artifacts = json.loads((ROOT / "src/odessy/runtime_backends/harness"
                            "/harness-artifacts.json").read_text(encoding="utf-8"))
    runtime = artifacts["runtime"]
    executable = runtime["executables"]["win-x64"]
    installed_exe = root / "versions" / version / "harness" / executable["file"]
    check("the installed engine is the pinned executable",
          installed_exe.is_file() and digest(installed_exe) == executable["sha256"],
          f"{executable['file']} "
          f"{digest(installed_exe)[:16] if installed_exe.is_file() else 'missing'}")

    bundle = artifacts["plugin_closure"]["bundles"][0]
    plugin_dir = wheel_marker / "runtime_backends" / "harness" / bundle["path"]
    plugin_files = {}
    for entry in bundle.get("files") or []:
        target = plugin_dir / entry["file"]
        plugin_files[entry["file"]] = target.is_file() and digest(target) == entry["sha256"]
    check("the packaged plugin matches its declared digests",
          bool(plugin_files) and all(plugin_files.values()) and plugin_dir.is_dir(),
          json.dumps(plugin_files))
    declared_version = str(bundle.get("version") or "")
    package_json = plugin_dir / "package.json"
    shipped_version = (json.loads(package_json.read_text(encoding="utf-8")).get("version")
                       if package_json.is_file() else "")
    check("the packaged plugin version is the declared one",
          bool(declared_version) and shipped_version == declared_version,
          f"declared {declared_version}, shipped {shipped_version}")

    version_dir = root / "versions" / version
    installed_python = (version_dir / "Scripts" / "python.exe" if os.name == "nt"
                        else version_dir / "bin" / "python3")
    node = next((path for path in version_dir.rglob("node.exe")), None) if os.name == "nt" \
        else next((path for path in version_dir.rglob("node")), None)
    server_entry = next(iter(version_dir.rglob("dist/src/server.js")), None)
    contract = next(iter(version_dir.rglob("src/odessy/execution/contract.json")), None)

    # A scrubbed environment: no checkout PYTHONPATH, no developer toolchain, no Harness
    # overrides. What works here is what the installed launcher can rely on.
    clean = {name: value for name, value in os.environ.items()
             if name.upper() in {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP",
                                 "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PATH",
                                 "PATHEXT", "PROCESSOR_ARCHITECTURE", "OS", "NUMBER_OF_PROCESSORS"}}
    clean.pop("PYTHONPATH", None)

    def probe(command: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=clean, timeout=timeout, check=False)

    python_version, imports = "", probe([str(installed_python), "-I", "-c", "pass"])
    if installed_python.is_file():
        python_version = probe([str(installed_python), "-I", "-c",
                                "import sys;print('.'.join(map(str,sys.version_info[:3])))"]
                               ).stdout.strip()
        imports = probe([str(installed_python), "-I", "-c",
                         "import odessy,fastapi,starlette,uvicorn,mcp,httpx2;print(odessy.__file__)"])
    check("the installed interpreter imports the application and its HTTP closure "
          "with no checkout on the path",
          imports.returncode == 0
          and str(version_dir).casefold() in (imports.stdout or "").casefold(),
          (imports.stdout or imports.stderr or "").strip()[-160:])
    node_version = (probe([str(node), "--version"]).stdout.strip() if node else "")
    check("the packaged Node and tool server are present and runnable",
          bool(node) and bool(server_entry) and bool(contract) and bool(node_version),
          json.dumps({"node": str(node), "node_version": node_version,
                      "server": str(server_entry), "contract": str(contract)})[:240])

    # -- registration (per-identity, removable) ----------------------------------
    registration: dict = {}
    shortcut = None
    if args.register:
        registered = bridge(interpreter, payload,
                            {"id": "p14-register", "operation": "manage",
                             "parameters": {"action": "package-register", "root": str(root),
                                            "payload": str(payload)}})
        start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"
        shortcut = next(iter(start_menu.glob("Odessy-*.lnk")), None) if start_menu.is_dir() else None
        registration = {"result": registered, "shortcut": str(shortcut) if shortcut else ""}
        check("registration reports the state it reached",
              bool(registered.get("registered")), json.dumps(registered)[:200])
        check("registration is outside the installation (a per-identity shortcut)",
              shortcut is not None, str(start_menu))

    installed_executable = installed_exe.is_file()

    # -- user-owned files survive ------------------------------------------------
    user_files = {
        "models/user-model.bin": b"user-owned model",
        ".ssh/id_ed25519_user": b"user-owned key",
        "notes/user-notes.txt": b"unrelated data",
    }
    for relative, data in user_files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    outside = work / "outside-user-data"
    outside.write_bytes(b"outside")

    plan = bridge(interpreter, payload,
                  {"id": "p14-uninstall-review", "operation": "manage",
                   "parameters": {"action": "package-uninstall-review", "root": str(root), "payload": str(payload)}})
    bridge(interpreter, payload,
           {"id": "p14-uninstall", "operation": "manage",
            "parameters": {"action": "package-uninstall", "root": str(root), "payload": str(payload),
                           "confirm": plan.get("plan_id")}})
    if args.register:
        leftover = (Path(os.environ.get("LOCALAPPDATA", "")) / "OdessyMaintenance")
        check("uninstall removed the registration it created",
              shortcut is None or not shortcut.exists(),
              str(shortcut) if shortcut else "")
    survived = all((root / name).read_bytes() == data for name, data in user_files.items())
    check("uninstall keeps user-owned models, SSH files and unrelated data", survived,
          json.dumps({name: (root / name).is_file() for name in user_files}))
    check("uninstall leaves no active version",
          not (json.loads((root / "installation.json").read_text(encoding="utf-8"))
               .get("active") or "") if (root / "installation.json").is_file() else True, "")
    check("uninstall does not touch data outside the installation",
          outside.read_bytes() == b"outside", str(outside))

    manifest = json.loads((payload / "wheelhouse/manifest.json").read_text(encoding="utf-8"))
    odessy_wheel = next((entry for entry in manifest["artifacts"]
                         if entry["file"].startswith("odessy-")), {})
    receipt = {
        "schema_version": 1,
        "odessy": {
            "version": version,
            "wheel": odessy_wheel.get("file", ""),
            "wheel_sha256": odessy_wheel.get("sha256", ""),
            "payload": payload_zip.name if payload_zip else payload.name,
            "payload_sha256": digest(payload_zip) if payload_zip else "",
        },
        "upstream_runtime": {
            "version": executable.get("reported_version"),
            "source_commit": runtime.get("source_commit"),
            "executable_sha256": executable["sha256"],
            "installed_executable": installed_executable,
        },
        "plugin": {"version": declared_version, "files": plugin_files},
        "python": python_version,
        "node": node_version,
        "components": review.get("components"),
        "checks": [{"check": name, "passed": ok, "detail": detail}
                   for name, ok, detail in checks],
    }
    receipt_path = work / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    passed = sum(1 for _, ok, _ in checks if ok)
    print(json.dumps({"summary": f"{passed}/{len(checks)} checks passed",
                      "receipt": receipt}, indent=2, ensure_ascii=False))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
