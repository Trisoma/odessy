"""The Harness engine's production inputs: the declared inventory and the shipped bundle.

Before this file existed the pin was a set of Python literals and the plugin bundle lived
under `experiments/`. Both were true of the checkout and neither was a declaration the
build could read, so an installed application could resolve a different set from the one
that was tested. These tests hold the three properties that make the declaration worth
having:

1. `pin.py` derives its enforced constants **from** `harness-artifacts.json`, so the digest
   a launch verifies and the digest an installer reads cannot disagree;
2. the bundle that ships is byte-identical to the declaration (a missing or edited file is
   reported, never silently replaced by another copy);
3. both travel inside the built wheel and resolve from an extracted payload with no
   checkout on `sys.path` — the packaging claim, measured rather than assumed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from odessy.runtime_backends.contracts import RuntimeUnavailable
from odessy.runtime_backends.harness import pin, resources
from odessy.runtime_backends.harness.config import HarnessConfig
from odessy.runtime_backends.harness.profile import NARROW_PATCH_NAME, NARROW_PATCH_TEXT

REPO_ROOT = Path(__file__).resolve().parents[1]
_HEX = set("0123456789abcdef")


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _HEX
    )


def test_the_pin_is_read_from_the_declared_inventory():
    """One declaration: the constants a launch enforces are the ones the file states."""

    inventory = resources.artifact_inventory()
    runtime = inventory["runtime"]

    assert pin.PINNED_VERSION == runtime["declared_version"] == "0.2.1-rc.2"
    assert pin.PINNED_SOURCE_TAG == runtime["source_tag"] == "dsh-v0.2.1-rc.2"
    assert pin.PINNED_SOURCE_COMMIT == runtime["source_commit"]
    assert pin.PINNED_PROFILE == runtime["profile"] == "acp"

    windows = runtime["executables"]["win-x64"]
    assert pin.PINNED_EXECUTABLE_SHA256 == windows["sha256"]
    assert pin.PINNED_EXECUTABLE_SIZE == windows["bytes"] == 273_157_120
    assert pin.PINNED_EXECUTABLE_PATH == windows["path_in_wheel"]
    assert pin.PINNED_EXECUTABLE_PATH.endswith(".exe")

    assert pin.PINNED_WHEEL_SHA256 == {
        str(tag): str(declared["sha256"])
        for tag, declared in runtime["wheels"].items()
    }


def test_every_declared_artifact_carries_an_identity_and_a_licence():
    """A declaration without a digest or a licence is not an inventory, it is a note."""

    inventory = resources.artifact_inventory()
    runtime = inventory["runtime"]
    assert runtime["license"], "the runtime distribution must declare its licence"

    for tag, wheel in runtime["wheels"].items():
        assert _is_digest(wheel["sha256"]), tag
        assert wheel["bytes"] > 0, tag
        assert wheel["platform"] and wheel["architecture"], tag
        assert wheel["file"].endswith(".whl"), tag
        # Whether a wheel was actually executed is part of the evidence, not a detail.
        assert isinstance(wheel["executed"], bool), tag

    for target, executable in runtime["executables"].items():
        assert _is_digest(executable["sha256"]), target
        assert executable["bytes"] > 0, target
        assert executable["reported_version"] == pin.PINNED_VERSION, target

    sdk = inventory["sdk"]
    assert _is_digest(sdk["wheel"]["sha256"])
    assert sdk["license"]
    # The production adapter speaks ACP to the executable and never imports the SDK; the
    # declaration says so instead of implying a dependency that does not exist.
    assert sdk["used_by_production"] is False

    for bundle in inventory["plugin_closure"]["bundles"]:
        assert bundle["license"], bundle["name"]
        assert bundle["files"], bundle["name"]
        for declared in bundle["files"]:
            assert _is_digest(declared["sha256"]), declared["file"]
            assert declared["bytes"] > 0, declared["file"]


def test_the_shipped_bundle_matches_its_declaration():
    """The bundle in this package is the declared one, file for file."""

    status = resources.bundle_status()
    assert status["problems"] == []
    assert status["present"] is True
    assert status["version"] == "0.0.3"
    assert [entry["file"] for entry in status["files"]] == [
        "package.json", "cordis.patch.yml", "lib/index.js",
    ]

    root = resources.bundle_root()
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    declared = resources.artifact_inventory()["plugin_closure"]["bundles"][0]
    assert manifest["name"] == declared["name"]
    assert manifest["version"] == declared["version"]
    # The patch entry is the key the runtime's own installer reads; a bundle without it
    # installs and activates nothing, which is exactly the kind of silent no-op this test
    # is here to prevent.
    patch = manifest["dsh"]["bundle"]["patch"]
    assert (root / patch).is_file()
    assert (root / declared["entry"]).is_file()


def test_a_missing_bundle_is_refused_rather_than_replaced(monkeypatch, tmp_path):
    """No fallback copy, no sibling checkout: absent means absent, and it is said out loud."""

    copy = tmp_path / "harness"
    shutil.copytree(resources.package_root(), copy)
    shutil.rmtree(copy / resources.BUNDLE_DIRECTORY)
    monkeypatch.setattr(resources, "package_root", lambda: copy)

    with pytest.raises(RuntimeUnavailable) as refused:
        resources.bundle_root()
    assert "missing" in refused.value.reason

    status = resources.bundle_status()
    assert status["present"] is False
    assert status["problems"], "a missing bundle must be reported, not passed over"
    assert any("missing" in problem for problem in status["problems"])


def test_a_changed_bundle_file_is_reported(monkeypatch, tmp_path):
    """An edited plugin is a different plugin; the declaration is what makes that visible."""

    copy = tmp_path / "harness"
    shutil.copytree(resources.package_root(), copy)
    edited = copy / resources.BUNDLE_DIRECTORY / resources.STREAM_BRIDGE / "lib" / "index.js"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n// not the reviewed file\n",
                      encoding="utf-8")
    monkeypatch.setattr(resources, "package_root", lambda: copy)

    status = resources.bundle_status()
    changed = [entry for entry in status["files"] if entry["file"] == "lib/index.js"]
    assert changed and changed[0]["digest_matches"] is False
    assert any("digest" in problem for problem in status["problems"])
    # The digest check is the only thing that noticed: the directory is still there.
    assert status["present"] is True


def test_the_launch_description_reports_the_packaged_inputs(tmp_path):
    """Diagnostics name the shipped bundle, so an installed-layout failure is visible."""

    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    home.mkdir()
    workspace.mkdir()
    overlay = home / NARROW_PATCH_NAME
    overlay.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
    config = HarnessConfig(
        command=(sys.executable,), home=home, workspace=workspace, narrow_patch=overlay,
    )

    described = config.describe()
    assert described["bundle"]["problems"] == []
    assert described["bundle"]["name"] == "@odessy/dsh-stream-bridge"
    assert Path(str(described["bundle"]["path"])).is_dir()


def test_the_wheel_ships_the_inventory_and_the_bundle(tmp_path):
    """The packaging claim: build, extract, import from the payload with no checkout path."""

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    try:
        import setuptools  # noqa: F401 - the build backend this project declares
    except ImportError:  # pragma: no cover - only on an interpreter without build tooling
        pytest.skip("this interpreter has no build backend, so the wheel cannot be built here")
    built = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(REPO_ROOT), "--no-deps",
         "--no-build-isolation", "-w", str(wheels)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    # A build failure is a finding, not a reason to pass quietly: the point of this test is
    # that the packaged inputs are reachable from what the build produces.
    assert built.returncode == 0, (built.stderr or "")[-1200:]

    artifact = next(wheels.glob("odessy-*.whl"))
    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
    harness = "odessy/runtime_backends/harness/"
    assert harness + "harness-artifacts.json" in names
    for relative in ("package.json", "cordis.patch.yml", "lib/index.js"):
        assert harness + "bundle/odessy-stream-bridge/" + relative in names, relative

    payload = tmp_path / "payload"
    with zipfile.ZipFile(artifact) as archive:
        archive.extractall(payload)

    probe = (
        "import json;"
        "from odessy.runtime_backends.harness import resources;"
        "print(json.dumps({"
        "'inventory': str(resources.artifacts_path()),"
        "'problems': resources.bundle_status()['problems'],"
        "'version': resources.bundle_status()['version'],"
        "}))"
    )
    environ = {name: value for name, value in os.environ.items() if name != "PYTHONHOME"}
    # Only the extracted payload: no `src`, no installed copy, no spike folder.
    environ["PYTHONPATH"] = str(payload)
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(tmp_path), env=environ,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert completed.returncode == 0, (completed.stderr or "")[-800:]
    reported = json.loads(completed.stdout.strip().splitlines()[-1])
    assert Path(reported["inventory"]).is_relative_to(payload)
    assert reported["problems"] == []
    assert reported["version"] == "0.0.3"
