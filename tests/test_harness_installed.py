"""The installed Harness payload: resolution, refusal and the composed profile.

Three claims are held here, and each one replaced an assumption:

1. **A launch finds the runtime it shipped with.** ``ODESSY_HARNESS_RUNTIME_DIR`` names a
   staged directory; every declared byte is checked against the same declaration ``pin.py``
   enforces, so a moved or edited payload refuses with the file and the digest instead of
   starting something else.
2. **Missing assets are actionable, not silent.** An unset variable names both accepted
   resolutions; a corrupt file names the file. Nothing falls back to another engine and
   nothing downloads at launch.
3. **The profile is composed, not built by pnpm.** The runtime composes ``profiles/<name>``
   from the bundle that ships inside the Odessy package, so an installed machine needs no
   package manager. Composition is idempotent, keeps an operator's own patch layer, and
   refuses rather than seeding a profile from a bundle that no longer matches.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from odessy.runtime_backends.contracts import RuntimeUnavailable
from odessy.runtime_backends.harness import resources
from odessy.runtime_backends.harness.config import from_environment

REPO_ROOT = Path(__file__).resolve().parents[1]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _declaration(executable: bytes, sidecar: bytes, name: str = "runtime-test.exe") -> dict:
    """A declaration shaped exactly like the real one, for files this test can create."""

    return {
        "file": name,
        "path_in_wheel": "pkg/runtime/" + name,
        "sha256": _digest(executable),
        "bytes": len(executable),
        "reported_version": "0.2.1-rc.2",
        "sidecars": [
            {"file": "side.txt", "path_in_wheel": "pkg/runtime/side.txt",
             "sha256": _digest(sidecar), "bytes": len(sidecar)},
        ],
    }


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    """A staged runtime directory plus a matching declaration."""

    runtime_dir = tmp_path / "Odessy Präfix Test" / "runtime" / "harness-runtime"
    runtime_dir.mkdir(parents=True)
    executable = b"MZ fake runtime"
    sidecar = b"fake sidecar"
    (runtime_dir / "runtime-test.exe").write_bytes(executable)
    (runtime_dir / "side.txt").write_bytes(sidecar)
    monkeypatch.setattr(resources, "executable_declaration",
                        lambda target="win-x64": _declaration(executable, sidecar))
    return runtime_dir


def test_a_staged_payload_is_verified_against_the_declaration(staged):
    assets = resources.verify_runtime_assets(staged)

    assert Path(assets["executable"]) == staged / "runtime-test.exe"
    assert assets["sidecars"] == ["side.txt"]
    assert assets["version"] == "0.2.1-rc.2"


def test_a_missing_sidecar_refuses_and_names_the_file(staged):
    (staged / "side.txt").unlink()

    with pytest.raises(RuntimeUnavailable) as caught:
        resources.verify_runtime_assets(staged)

    detail = str(caught.value.detail)
    assert "side.txt" in detail and "missing" in detail


def test_an_edited_executable_refuses_with_the_digest(staged):
    # Same length, different bytes: the size check cannot mask the digest check.
    (staged / "runtime-test.exe").write_bytes(b"MZ FAKE RUNTIME")

    with pytest.raises(RuntimeUnavailable) as caught:
        resources.verify_runtime_assets(staged)

    assert "sha256" in str(caught.value.detail)


def test_the_installed_executable_is_resolved_from_the_staged_directory(staged):
    resolved = resources.installed_executable({"ODESSY_HARNESS_RUNTIME_DIR": str(staged)})

    assert resolved == staged / "runtime-test.exe"


def test_no_configuration_names_both_resolutions(staged):
    with pytest.raises(RuntimeUnavailable) as caught:
        resources.installed_executable({})

    detail = str(caught.value.detail)
    assert resources.INSTALLED_ENV in detail and "ODESSY_HARNESS_EXE" in detail


def test_a_launch_prefers_the_explicit_override_and_never_mixes_the_two(staged, tmp_path):
    """``ODESSY_HARNESS_EXE`` is the developer override; a broken installation must not
    stop it, and an installed launch must not be silently redirected to it."""

    explicit = tmp_path / "dev-runtime.exe"
    explicit.write_bytes(b"MZ")

    config = from_environment({
        "ODESSY_HARNESS_EXE": str(explicit),
        "ODESSY_HARNESS_RUNTIME_DIR": str(tmp_path / "not-installed"),
        "ODESSY_HARNESS_HOME": str(tmp_path / "home"),
        "ODESSY_HARNESS_TOOLS": "",
    }, workspace=REPO_ROOT)

    assert config.command == (str(explicit),)


def test_an_installed_launch_resolves_the_staged_executable(staged, tmp_path):
    config = from_environment({
        "ODESSY_HARNESS_RUNTIME_DIR": str(staged),
        "ODESSY_HARNESS_HOME": str(tmp_path / "home"),
    }, workspace=REPO_ROOT)

    assert config.command == (str(staged / "runtime-test.exe"),)
    assert config.expected_version == "0.2.1-rc.2"
    # The profile was composed for this launch, and the record says so.
    assert "package.json" in config.profile_record["wrote"]
    assert config.profile_record["bundle"] == "installed"


def test_the_private_node_is_reported_only_when_it_is_staged(staged):
    node = staged.parent / "node-runtime" / "node.exe"
    assert resources.installed_node({"ODESSY_HARNESS_RUNTIME_DIR": str(staged)}) is None

    node.parent.mkdir(parents=True)
    node.write_bytes(b"MZ node")

    assert resources.installed_node({"ODESSY_HARNESS_RUNTIME_DIR": str(staged)}) == node
    assert resources.installed_node({}) is None


def test_a_versioned_install_reports_its_adjacent_node_and_tools(tmp_path):
    """The packaged/versioned layout stages ``harness/`` next to ``node/`` and ``tools/``."""

    version = tmp_path / "versions" / ("a" * 32)
    harness = version / "harness"
    harness.mkdir(parents=True)
    node = version / "node" / "node.exe"
    node.parent.mkdir()
    node.write_bytes(b"MZ node")
    entry = version / "tools" / "dist" / "src" / "server.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// entry")

    environ = {"ODESSY_HARNESS_RUNTIME_DIR": str(harness)}

    assert resources.installed_node(environ) == node
    assert resources.installed_node_tools_dir(environ) == version / "tools"


def test_the_tool_server_requires_a_named_or_installed_node(staged, tmp_path):
    environ = {
        "ODESSY_HARNESS_RUNTIME_DIR": str(staged),
        "ODESSY_HARNESS_HOME": str(tmp_path / "home"),
        "ODESSY_HARNESS_TOOLS": "on",
        "ODESSY_HARNESS_NODE_TOOLS": "on",
    }

    with pytest.raises(RuntimeUnavailable) as caught:
        from_environment(environ, workspace=REPO_ROOT)

    detail = str(caught.value.detail)
    assert "ODESSY_HARNESS_NODE" in detail and "node-runtime" in detail


# ---------------------------------------------------------------------------------------
# The composed profile
# ---------------------------------------------------------------------------------------

def _profile_dir(tmp_path: Path) -> Path:
    return tmp_path / "home" / "profiles" / "acp"


def _packaged_bundle_files() -> list[dict]:
    return resources._declared_bundle(resources.STREAM_BRIDGE)["files"]


def test_a_fresh_profile_is_composed_from_the_packaged_bundle(tmp_path):
    profile_dir = _profile_dir(tmp_path)

    record = resources.compose_profile(profile_dir)

    assert record["bundle"] == "installed"
    assert set(record["wrote"]) == {"package.json", "cordis.yml", "cordis.patch.yml", "bundle"}
    document = json.loads((profile_dir / "package.json").read_text(encoding="utf-8"))
    assert document["dsh"]["profile"]["bundles"] == resources.declared_profile()["bundles"]
    assert resources.OWN_BUNDLE_PACKAGE in document["dsh"]["profile"]["bundles"]
    for declared in _packaged_bundle_files():
        copied = profile_dir / "node_modules/@odessy/dsh-stream-bridge" / declared["file"]
        assert copied.is_file()
        assert _digest(copied.read_bytes()) == declared["sha256"]


def test_composing_twice_writes_nothing(tmp_path):
    profile_dir = _profile_dir(tmp_path)
    resources.compose_profile(profile_dir)

    record = resources.compose_profile(profile_dir)

    assert record["wrote"] == []
    assert record["bundle"] == "current"
    assert "package.json" in record["kept"]


def test_an_operator_patch_layer_is_never_overwritten(tmp_path):
    profile_dir = _profile_dir(tmp_path)
    resources.compose_profile(profile_dir)
    (profile_dir / "cordis.patch.yml").write_text("- id: mine\n", encoding="utf-8")

    record = resources.compose_profile(profile_dir)

    assert "cordis.patch.yml" in record["kept"]
    assert (profile_dir / "cordis.patch.yml").read_text(encoding="utf-8") == "- id: mine\n"


def test_a_profile_with_other_bundles_keeps_them_and_so_is_reported(tmp_path):
    profile_dir = _profile_dir(tmp_path)
    profile_dir.mkdir(parents=True)
    (profile_dir / "package.json").write_text(json.dumps({
        "name": "dsh-profile-acp",
        "private": True,
        "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "@someone/extra"]}},
    }), encoding="utf-8")

    record = resources.compose_profile(profile_dir)

    assert record["added_bundle"] is True
    document = json.loads((profile_dir / "package.json").read_text(encoding="utf-8"))
    assert document["dsh"]["profile"]["bundles"] == [
        "@deepseek-ai/dsh-base", "@someone/extra", resources.OWN_BUNDLE_PACKAGE]


def test_a_damaged_bundle_refuses_instead_of_seeding_a_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "bundle_status",
                        lambda name=resources.STREAM_BRIDGE: {"problems": ["lib/index.js is missing"]})

    with pytest.raises(RuntimeUnavailable) as caught:
        resources.compose_profile(_profile_dir(tmp_path))

    assert "lib/index.js is missing" in str(caught.value.detail)
    assert not (_profile_dir(tmp_path) / "package.json").exists()


def test_the_declared_profile_names_odessys_own_bundle():
    declared = resources.declared_profile()

    assert declared["name"] == "acp"
    assert resources.OWN_BUNDLE_PACKAGE in declared["bundles"]
    # Upstream bundles first: the pin composes them in order and Odessy patches after.
    assert declared["bundles"][0] == "@deepseek-ai/dsh-base"
    assert declared["bundle_directory"] == "node_modules/@odessy/dsh-stream-bridge"

def test_installed_node_tools_are_under_the_application_not_the_root(tmp_path):
    """The assembler puts the reviewed tool server at <root>/application/tools.

    The resolver used to look at <root>/tools, which never exists in an assembled
    installation; a root-level directory with that name must not be preferred either.
    """

    root = tmp_path / "Odessy Test Äpp"
    runtime = root / "runtime" / "harness-runtime"
    tools = root / "application" / "tools"
    tools.mkdir(parents=True)
    runtime.mkdir(parents=True)
    environ = {"ODESSY_HARNESS_RUNTIME_DIR": str(runtime)}

    assert resources.installed_node_tools_dir(environ) == tools
    (root / "tools").mkdir()
    assert resources.installed_node_tools_dir(environ) == tools
    assert resources.installed_node_tools_dir({}) is None


# ---------------------------------------------------------------------------------------
# The installed launch names the payload that ships beside it
# ---------------------------------------------------------------------------------------

def _launcher(root: Path):
    """The launcher as it is installed: the template copied beside the application."""

    import importlib.util
    import shutil

    target = Path(root) / "launch.py"
    shutil.copyfile(REPO_ROOT / "installer" / "templates" / "launch.py", target)
    spec = importlib.util.spec_from_file_location("odessy_installed_launcher", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _launcher_root(tmp_path: Path, *, record: bool) -> Path:
    """An installed application whose paths contain a space and a non-ASCII character."""

    import sys

    root = tmp_path / "Odessy Test Äpp"
    runtime = root / "runtime" / "harness-runtime"
    runtime.mkdir(parents=True)
    executable = runtime / resources.executable_declaration()["file"]
    executable.write_bytes(b"MZ installed runtime")
    node = root / "runtime" / "node-runtime" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"MZ node")
    entry = root / "application" / "tools" / "dist" / "src" / "server.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// entry", encoding="utf-8")
    (root / "src" / "odessy").mkdir(parents=True)
    (root / "src" / "odessy" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "odessy" / "__main__.py").write_text("", encoding="utf-8")
    settings = {"schema_version": 1, "status": "ready", "runtime_dir": str(root / "runtime"),
                "dependency_dir": str(root / "dependencies"), "python": sys.executable,
                "runtime_versions": {}, "source_sha256": {}, "tool_sha256": "0" * 64}
    if record:
        settings["harness"] = {
            "runtime_dir": str(runtime), "executable": str(executable),
            "node_executable": str(node), "node_tools_dir": str(root / "application" / "tools")}
    (root / "odessy-application.json").write_text(json.dumps(settings), encoding="utf-8")
    return root


def _launch_and_capture(root: Path, monkeypatch) -> dict:
    """Run the launcher's own setup and report the environment it would hand to Odessy."""

    import os
    import runpy

    launch = _launcher(root)
    called: dict = {}
    monkeypatch.setattr(runpy, "run_module", lambda name, **kw: called.update(name=name, **kw))
    for name in ("ODESSY_NODE_TOOLS_DIR", "ODESSY_HARNESS_NODE", "ODESSY_HARNESS_RUNTIME_DIR"):
        monkeypatch.delenv(name, raising=False)
    assert launch.main([]) == 0
    assert called["name"] == "odessy"
    return {name: os.environ[name] for name in
            ("ODESSY_NODE_TOOLS_DIR", "ODESSY_HARNESS_NODE", "ODESSY_HARNESS_RUNTIME_DIR")
            if name in os.environ}


@pytest.mark.parametrize("record", [True, False])
def test_the_installed_launcher_exports_the_staged_layout(tmp_path, monkeypatch, record):
    """No external override: the launcher itself names the runtime, its Node and the tools.

    The recorded case is what this installer assembles. The unrecorded case is a marker
    written before the component shipped; it must resolve through the same layout rule as
    the engine (``<root>/application/tools`` beside ``<root>/runtime/harness-runtime``).
    """

    root = _launcher_root(tmp_path, record=record)

    exported = _launch_and_capture(root, monkeypatch)

    assert exported["ODESSY_HARNESS_RUNTIME_DIR"] == str(root / "runtime" / "harness-runtime")
    assert exported["ODESSY_HARNESS_NODE"] == str(root / "runtime" / "node-runtime" / "node.exe")
    assert exported["ODESSY_NODE_TOOLS_DIR"] == str(root / "application" / "tools")


def test_the_runtime_resolution_alone_agrees_with_the_launcher(tmp_path, monkeypatch):
    """Given only the runtime directory, both resolvers name the same installed files."""

    root = _launcher_root(tmp_path, record=True)
    environ = {"ODESSY_HARNESS_RUNTIME_DIR": str(root / "runtime" / "harness-runtime")}

    assert resources.installed_node(environ) == root / "runtime" / "node-runtime" / "node.exe"
    assert resources.installed_node_tools_dir(environ) == root / "application" / "tools"
    assert _launch_and_capture(root, monkeypatch)["ODESSY_NODE_TOOLS_DIR"] == str(
        root / "application" / "tools")


def test_the_engine_finds_the_staged_tool_server_without_the_override(tmp_path):
    """The runtime's own resolution is enough to name a usable Node tool server."""

    from odessy.runtime_backends.harness import config as harness_config

    root = tmp_path / "Odessy Test Äpp"
    node = root / "runtime" / "node-runtime" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"MZ node")
    runtime = root / "runtime" / "harness-runtime"
    runtime.mkdir(parents=True)
    entry = root / "application" / "tools" / "dist" / "src" / "server.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// entry", encoding="utf-8")

    tools = harness_config._node_tools({
        "ODESSY_HARNESS_RUNTIME_DIR": str(runtime),
        "ODESSY_HARNESS_NODE_TOOLS": "on",
    })

    assert tools is not None
    assert Path(tools.executable) == node
    assert Path(tools.entry) == entry
