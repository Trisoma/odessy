"""Installation-plan preview. Standard library only; no downloads or services."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class Location:
    path: str
    existing_parent: str | None
    free_bytes: int | None
    writable: bool
    error: str | None = None


def default_locations(env: Mapping[str, str], home: Path) -> tuple[Path, Path]:
    local = env.get("LOCALAPPDATA")
    if not local:
        local = str(home / "AppData" / "Local")
    return Path(local) / "Programs" / "Odessy", Path(local) / "Odessy" / "models"


def inspect_location(path: Path) -> Location:
    """Probe the nearest existing directory using a temporary, deleted file.

    Does not create the installation directory. A write probe is necessary because
    os.access alone cannot establish effective Windows ACL permissions.
    """
    requested = str(path)
    try:
        if not path.is_absolute():
            raise ValueError("Choose an absolute directory path.")
        target = path.resolve()
        parent = target
        while not parent.exists():
            if parent == parent.parent:
                raise OSError("No existing parent directory was found.")
            parent = parent.parent
        if not parent.is_dir():
            raise ValueError("The target or one of its parents is a file, not a directory.")
        free = shutil.disk_usage(parent).free
        with tempfile.TemporaryFile(prefix=".odessy-preflight-", dir=parent) as probe:
            probe.write(b"odessy installation directory probe\n")
            probe.flush()
        return Location(str(target), str(parent), free, True)
    except (OSError, ValueError, RuntimeError) as error:
        return Location(requested, None, None, False, str(error))


def repository_defaults(path: Path) -> dict[str, str]:
    """Read only non-secret model defaults from the committed configuration.

    This intentionally does not load user configuration or mutate os.environ.
    """
    allowed = {"ODESSY_MODEL_NAME", "ODESSY_MODEL_ENDPOINT"}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in allowed:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            result[key] = value
    missing = {key for key in allowed if not result.get(key)}
    if missing:
        raise ValueError("Missing repository model defaults: " + ", ".join(sorted(missing)))
    return result


def build_report(
    install_dir: Path,
    model_dir: Path,
    *,
    env: Mapping[str, str],
    home: Path,
    system: str,
    machine: str,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path | None = None,
    inspect: Callable[[Path], Location] = inspect_location,
) -> dict:
    checks: list[Check] = []
    supported = system == "Windows" and machine.lower() in {"amd64", "x86_64"}
    checks.append(Check("platform", "ok" if supported else "fail",
                        f"Detected {system}/{machine}; this installer targets Windows x64."))
    install = inspect(install_dir)
    models = inspect(model_dir)
    for name, location in (("install_directory", install), ("model_directory", models)):
        checks.append(Check(name, "ok" if location.writable else "fail",
                            f"Temporary write probe passed: {location.path}" if location.writable
                            else f"Directory check failed: {location.error}"))
    config = env.get("ODESSY_CONFIG_FILE")
    # Match the runtime: an explicit relative configuration path remains relative
    # to the launch directory, rather than silently becoming relative to home.
    config_path = Path(config) if config else home / ".config" / "odessy" / "odessy.env"
    checks.append(Check("configuration", "warn" if config_path.exists() else "info",
                        f"Configuration target: {config_path}; "
                        + ("existing file will be preserved." if config_path.exists()
                           else "no file will be created by this preview.")))
    components = [
        {"id": "python", "purpose": "Application-private Python and virtual environment",
         "source": "pyproject.toml: project.requires-python"},
        {"id": "python_dependencies", "purpose": "Locked Python runtime dependencies",
         "source": "pyproject.toml: project.dependencies (release lock pending)"},
        {"id": "node", "purpose": "Application-private Node.js and npm",
         "source": "README.md: Quick Start; .github/workflows/ci.yml"},
        {"id": "tool_server", "purpose": "Prebuilt Windows Node tool server with dependencies",
         "source": "tools/package.json; tools/package-lock.json"},
        {"id": "ollama", "purpose": "Reuse a compatible service or prepare a managed copy",
         "source": "README.md: Quick Start"},
        {"id": "model", "purpose": "Download the selected local model after confirmation",
         "source": "config/odessy.env"},
    ]
    try:
        defaults = repository_defaults(repo_root / "config" / "odessy.env")
    except (OSError, ValueError) as error:
        defaults = {}
        checks.append(Check("repository_defaults", "fail", str(error)))
    selected_manifest = None
    if manifest_path is not None:
        from installer.core.manifest import ManifestError, load_manifest
        try:
            manifest = load_manifest(manifest_path)
            selected_manifest = {
                "release": manifest.release,
                "download_bytes": manifest.download_bytes,
                "artifacts": [{"id": a.id, "version": a.version, "size_bytes": a.size_bytes,
                               "sha256": a.sha256} for a in manifest.artifacts],
            }
            checks.append(Check("selected_download_manifest", "ok",
                                f"Validated {len(manifest.artifacts)} file(s), {manifest.download_bytes} download bytes; "
                                "this does not establish application completeness or publisher authenticity."))
        except ManifestError as error:
            checks.append(Check("selected_download_manifest", "fail", str(error)))
    checks.append(Check("download_metadata", "pending",
                        "A complete application release with locked dependencies is not prepared yet. "
                        "Individual manifests can be previewed or downloaded with python -m installer.download."))
    checks.append(Check("disk_budget", "pending",
                        "Free space is reported, but sufficiency is NOT verified until download, "
                        "extraction, model and rollback sizes are known. Shared volumes must be budgeted together."))
    return {
        "schema_version": 1,
        "milestone": "preflight-preview",
        "target": "windows-x64-single-local-text",
        "preflight_passed": not any(c.status == "fail" for c in checks),
        "installation_ready": False,
        "install_location": asdict(install),
        "model_location": asdict(models),
        "download_manifest": selected_manifest,
        "configuration": {"path": str(config_path), "exists": config_path.exists(), "modified": False},
        "repository_model_defaults": defaults,
        "components": components,
        "checks": [asdict(check) for check in checks],
    }


def render(report: dict) -> str:
    lines = ["Odessy installer - installation plan preview", ""]
    for check in report["checks"]:
        lines.append(f"[{check['status'].upper()}] {check['name']}: {check['message']}")
    for key in ("install_location", "model_location"):
        location = report[key]
        if location["free_bytes"] is not None:
            lines.append(f"{key} free space: {location['free_bytes'] / 1024**3:.2f} GiB")
    lines.extend(["", "Planned components:"])
    lines.extend(f"- {c['id']}: {c['purpose']} ({c['source']})" for c in report["components"])
    model = report["repository_model_defaults"].get("ODESSY_MODEL_NAME", "not available")
    lines.extend([f"Repository default model: {model}", "",
                  "PREVIEW ONLY: no software downloaded, installed, launched or configured.",
                  "Temporary directory-write probes have been removed.",
                  "Installation is not ready: release artifacts and size budgets are pending."])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    home = Path.home()
    install_dir, model_dir = default_locations(os.environ, home)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=install_dir)
    parser.add_argument("--model-dir", type=Path, default=model_dir)
    parser.add_argument("--manifest", type=Path, help="Optionally validate a local download manifest.")
    parser.add_argument("--json", action="store_true", help="Print a structured report to stdout.")
    args = parser.parse_args(argv)
    report = build_report(args.install_dir, args.model_dir, env=os.environ, home=home,
                          manifest_path=args.manifest,
                          system=platform.system(),
                          machine=os.environ.get("PROCESSOR_ARCHITEW6432") or platform.machine())
    print(json.dumps(report, ensure_ascii=True, indent=2) if args.json else render(report))
    # Zero means this limited preflight passed, never that installation completed.
    return 0 if report["preflight_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
