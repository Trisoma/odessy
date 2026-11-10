"""The installer's Harness component: what it stages, and what makes it refuse.

The installer is the only place where "the runtime is installed" becomes true, so these
tests are about the two failure modes a packaged runtime actually has:

- the downloaded artifact is not the declared one (wrong digest, wrong member set), and
- the staged payload is not what the declaration says afterwards (missing sidecar, edited
  executable, a marker from another version).

Both must fail *packaging*, not a launch hours later. The happy path is asserted by
re-reading the staged directory against the same declaration the launch enforces, so the
installer and the runtime cannot disagree about what "installed" means.
"""

from __future__ import annotations

import hashlib
import json
import types
import zipfile
from pathlib import Path

import pytest

from installer.components import harness

REPO_ROOT = Path(__file__).resolve().parents[1]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _declaration(engine: bytes, sidecar: bytes, licence: bytes, *, wheel_sha: str = "0" * 64,
                 wheel_bytes: int = 1) -> dict:
    prefix = "deepseek_harness_runtime"
    return {
        "schema": 1,
        "runtime": {
            "declared_version": "0.2.1-rc.2",
            "source_tag": "dsh-v0.2.1-rc.2",
            "source_commit": "0" * 40,
            "wheels": {
                harness.WHEEL_TAG: {
                    "file": "fake-0.2.1rc2-py3-none-win_amd64.whl",
                    "sha256": wheel_sha,
                    "bytes": wheel_bytes,
                    "url": "https://example.invalid/fake-0.2.1rc2-py3-none-win_amd64.whl",
                    "archive_prefix": prefix,
                },
            },
            "executables": {
                harness.TARGET: {
                    "file": "engine.exe",
                    "path_in_wheel": prefix + "/runtime/engine.exe",
                    "sha256": _digest(engine),
                    "bytes": len(engine),
                    "reported_version": "0.2.1-rc.2",
                    "sidecars": [{
                        "file": "side.txt",
                        "path_in_wheel": prefix + "/runtime/side.txt",
                        "sha256": _digest(sidecar),
                        "bytes": len(sidecar),
                    }],
                },
            },
            "licenses": [{
                "file": "LICENSE",
                "path_in_wheel": "fake-0.2.1rc2.dist-info/licenses/LICENSE",
                "sha256": _digest(licence),
                "bytes": len(licence),
            }],
        },
    }


@pytest.fixture()
def fake_wheel(tmp_path):
    engine, sidecar, licence = b"MZ engine", b"sidecar", b"MIT"
    prefix = "deepseek_harness_runtime"
    wheel = tmp_path / "fake.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(prefix + "/runtime/engine.exe", engine)
        archive.writestr(prefix + "/runtime/side.txt", sidecar)
        archive.writestr("fake-0.2.1rc2.dist-info/licenses/LICENSE", licence)
        # An undeclared member must not be extracted, even though it is in the archive.
        archive.writestr(prefix + "/runtime/extra-unreviewed.exe", b"MZ extra")
    data = wheel.read_bytes()
    return wheel, _declaration(engine, sidecar, licence, wheel_sha=_digest(data),
                               wheel_bytes=len(data))


def _prepare(fake_wheel, tmp_path, *, probe=None):
    wheel, declaration = fake_wheel
    target = tmp_path / "install" / "runtime" / "harness-runtime"
    download = lambda artifact, cache: types.SimpleNamespace(path=wheel)
    marker = harness.prepare(declaration, target, tmp_path / "cache", download=download,
                             probe=probe or (lambda executable, expected, **kw: expected))
    return declaration, target, marker


def test_the_declared_files_are_the_files_that_land_on_disk(fake_wheel, tmp_path):
    declaration, target, marker = _prepare(fake_wheel, tmp_path)

    assert (target / "engine.exe").read_bytes() == b"MZ engine"
    assert (target / "side.txt").read_bytes() == b"sidecar"
    assert (target / "licenses" / "LICENSE").read_bytes() == b"MIT"
    # An archive entry the declaration does not name is never written.
    assert not (target / "extra-unreviewed.exe").exists()
    assert marker["version"] == "0.2.1-rc.2"
    assert [row["file"] for row in marker["files"]] == [
        "engine.exe", "side.txt", "licenses/LICENSE"]
    assert harness.verify(target, declaration)["files_verified"] == 3


def test_a_member_with_the_wrong_digest_refuses_before_the_marker_exists(fake_wheel, tmp_path):
    wheel, declaration = fake_wheel
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("deepseek_harness_runtime/runtime/engine.exe", b"MZ engine")
        archive.writestr("deepseek_harness_runtime/runtime/side.txt", b"sidecaX")
        archive.writestr("fake-0.2.1rc2.dist-info/licenses/LICENSE", b"MIT")
    rebaked = wheel.read_bytes()
    declaration["runtime"]["wheels"][harness.WHEEL_TAG]["sha256"] = _digest(rebaked)
    declaration["runtime"]["wheels"][harness.WHEEL_TAG]["bytes"] = len(rebaked)

    with pytest.raises(harness.HarnessPreparationError) as caught:
        _prepare((wheel, declaration), tmp_path)

    assert "digest" in str(caught.value)
    assert not (tmp_path / "install" / "runtime" / "harness-runtime" / harness.MARKER).exists()


def test_a_missing_member_refuses(fake_wheel, tmp_path):
    wheel, declaration = fake_wheel
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("deepseek_harness_runtime/runtime/engine.exe", b"MZ engine")
        archive.writestr("fake-0.2.1rc2.dist-info/licenses/LICENSE", b"MIT")
    rebaked = wheel.read_bytes()
    declaration["runtime"]["wheels"][harness.WHEEL_TAG]["sha256"] = _digest(rebaked)
    declaration["runtime"]["wheels"][harness.WHEEL_TAG]["bytes"] = len(rebaked)

    with pytest.raises(harness.HarnessPreparationError) as caught:
        _prepare((wheel, declaration), tmp_path)

    assert "missing a declared member" in str(caught.value)


def test_an_engine_that_reports_another_version_refuses(fake_wheel, tmp_path):
    def probe(executable, expected, **kwargs):
        raise harness.HarnessPreparationError(f"expected {expected}, got '0.2.0'")

    with pytest.raises(harness.HarnessPreparationError):
        _prepare(fake_wheel, tmp_path, probe=probe)


def test_an_edited_payload_after_staging_refuses(fake_wheel, tmp_path):
    declaration, target, _marker = _prepare(fake_wheel, tmp_path)
    (target / "side.txt").write_bytes(b"changed")

    with pytest.raises(harness.HarnessPreparationError) as caught:
        harness.verify(target, declaration)

    assert "side.txt" in str(caught.value)


def test_a_missing_component_refuses(fake_wheel, tmp_path):
    declaration, target, _marker = _prepare(fake_wheel, tmp_path)
    (target / harness.MARKER).unlink()

    with pytest.raises(harness.HarnessPreparationError) as caught:
        harness.verify(target, declaration)

    assert "not installed" in str(caught.value)


def test_a_marker_from_another_version_refuses(fake_wheel, tmp_path):
    declaration, target, _marker = _prepare(fake_wheel, tmp_path)
    marker = json.loads((target / harness.MARKER).read_text(encoding="utf-8"))
    marker["version"] = "0.2.0"
    (target / harness.MARKER).write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(harness.HarnessPreparationError) as caught:
        harness.verify(target, declaration)

    assert "another version" in str(caught.value)


# ---------------------------------------------------------------------------------------
# The real declaration, and where the component sits in the installer
# ---------------------------------------------------------------------------------------

def test_the_real_declaration_names_a_complete_payload():
    declaration = harness.load_declaration()
    members = harness._members(declaration)

    installed = [path for _member, path, _digest, _size in members]
    assert "deepseek-harness-sdk-runtime-win-x64.exe" in installed
    assert "deepseek-harness-sdk-runtime-win-x64-rg.exe" in installed
    assert "win-x64/downloads.json" in installed
    assert {"licenses/LICENSE", "licenses/THIRD_PARTY_NOTICES.md"} <= set(installed)
    assert harness.unpacked_bytes(declaration) == sum(size for *_rest, size in members)


def test_the_real_wheel_artifact_carries_the_production_pin():
    declaration = harness.load_declaration()
    artifact = harness.wheel_artifact(declaration)

    runtime = declaration["runtime"]
    assert artifact.version == runtime["declared_version"] == "0.2.1-rc.2"
    assert artifact.sha256 == runtime["wheels"][harness.WHEEL_TAG]["sha256"]
    assert artifact.size_bytes == runtime["wheels"][harness.WHEEL_TAG]["bytes"]
    assert artifact.url.startswith("https://files.pythonhosted.org/")


def test_the_installed_paths_point_inside_the_installation():
    paths = harness.installed_paths(Path("C:/Odessy installation"))

    assert paths["executable"].endswith("harness-runtime\\deepseek-harness-sdk-runtime-win-x64.exe")
    assert paths["node_executable"].endswith("node-runtime\\node.exe")
    assert paths["node_tools_dir"].endswith("application\\tools")


def test_the_workflow_stages_the_harness_before_the_application():
    from installer import workflow

    names = list(workflow.STEP_NAMES)
    assert "harness_runtime" in names
    assert names.index("runtime") < names.index("harness_runtime") < names.index("application")

    paths = workflow.paths_for(Path("C:/root"))
    assert paths["harness"] == Path("C:/root") / "runtime" / harness.COMPONENT_DIRECTORY


def test_the_version_probe_isolates_state_and_scrubs_credentials(tmp_path, monkeypatch):
    """``--version`` runs against a throw-away DSH_HOME that is removed afterwards.

    The probe is a staging check: it must not read or write the user's Harness state, and
    it must not inherit a route credential or a bridge token.
    """
    from types import SimpleNamespace
    from installer.components import harness as runtime_harness

    observed = {}

    def fake_run(argv, **kwargs):
        home = kwargs["env"]["DSH_HOME"]
        observed["home"] = home
        observed["exists_during_call"] = Path(home).is_dir()
        observed["env"] = dict(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="0.2.1\n", stderr="")

    monkeypatch.setenv("ODESSY_CLOUD_API_KEY", "must-not-be-inherited")
    monkeypatch.setenv("ODESSY_BRIDGE_TOKEN", "must-not-be-inherited")
    monkeypatch.setattr(runtime_harness.subprocess, "run", fake_run)

    reported = runtime_harness.probe_version(Path("fake.exe"), "0.2.1")

    assert reported == "0.2.1"
    assert "odessy-harness-probe-" in observed["home"]
    assert observed["exists_during_call"] is True
    assert not Path(observed["home"]).exists()
    assert "ODESSY_CLOUD_API_KEY" not in observed["env"]
    assert "ODESSY_BRIDGE_TOKEN" not in observed["env"]
