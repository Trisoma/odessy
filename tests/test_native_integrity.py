"""Integrity checks for the packaged native dependency chain."""

import hashlib
import json

from odessy.execution.native_integrity import (
    locked_package_version,
    verify_package_version,
    verify_receipt_hashes,
    verify_sha256_file,
)
from odessy.execution.providers.windows_acl import PINNED_KOFFI_VERSION, WindowsAclProvider, windows_acl_runner


def _write_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_verify_sha256_file_matches_and_rejects(tmp_path):
    target = tmp_path / "native.node"
    target.write_bytes(b"trusted bytes")
    digest = hashlib.sha256(b"trusted bytes").hexdigest()
    assert verify_sha256_file(target, digest) is True
    assert verify_sha256_file(target, "0" * 64) is False
    assert verify_sha256_file(target, "not-a-digest") is False
    assert verify_sha256_file(tmp_path / "missing", digest) is False


def test_verify_receipt_hashes_all_or_nothing(tmp_path):
    base = tmp_path / "node-pty"
    _write_file(base / "a.node", "a")
    _write_file(base / "b.node", "b")
    a = hashlib.sha256(b"a").hexdigest()
    b = hashlib.sha256(b"b").hexdigest()
    entries = [
        {"path": "a.node", "sha256": a},
        {"path": "b.node", "sha256": b},
    ]
    ok, failed = verify_receipt_hashes(entries, base)
    assert ok and failed == ()

    tampered = [{"path": "a.node", "sha256": "0" * 64}, entries[1]]
    ok, failed = verify_receipt_hashes(tampered, base)
    assert not ok and failed == ("a.node",)


def test_verify_receipt_hashes_rejects_empty_and_malformed(tmp_path):
    ok, failed = verify_receipt_hashes([], tmp_path)
    assert not ok and failed == ("<empty receipt>",)
    ok, failed = verify_receipt_hashes([{"path": 1, "sha256": None}], tmp_path)
    assert not ok


def test_locked_package_version_and_anchor(tmp_path):
    _write_file(tmp_path / "koffi" / "package.json", json.dumps({"version": "3.3.2"}))
    assert locked_package_version(tmp_path, "koffi") == "3.3.2"
    ok, detail = verify_package_version(tmp_path, "koffi", "3.3.2")
    assert ok and "3.3.2" in detail

    ok, detail = verify_package_version(tmp_path, "koffi", "9.9.9")
    assert not ok and "does not match" in detail

    ok, detail = verify_package_version(tmp_path, "ghost", "3.3.2")
    assert not ok and "missing" in detail


def test_windows_acl_runner_discovery_rejects_wrong_koffi_version(tmp_path, monkeypatch):
    """The discovered runner is refused when koffi was replaced after install."""
    tools = tmp_path / "tools"
    runner = tools / "dist" / "src" / "execution" / "win32" / "runner.js"
    _write_file(runner, "// built runner")
    _write_file(tools / "node_modules" / "koffi" / "package.json", json.dumps({"version": "9.9.9"}))

    monkeypatch.setattr("odessy.tools.node_mcp._node_tools_dir", lambda root: tools)
    monkeypatch.setattr("odessy.tools.node_mcp._public_repo_root", lambda: tmp_path)

    assert windows_acl_runner() is None
    assert windows_acl_runner(verify_dependencies=False) == runner


def test_windows_acl_runner_accepts_pinned_version(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    runner = tools / "dist" / "src" / "execution" / "win32" / "runner.js"
    _write_file(runner, "// built runner")
    _write_file(tools / "node_modules" / "koffi" / "package.json",
                json.dumps({"version": PINNED_KOFFI_VERSION}))

    monkeypatch.setattr("odessy.tools.node_mcp._node_tools_dir", lambda root: tools)
    monkeypatch.setattr("odessy.tools.node_mcp._public_repo_root", lambda: tmp_path)

    assert windows_acl_runner() == runner


def test_injected_runner_skips_discovery_check_used_by_tests(tmp_path):
    """An explicitly injected runner stays trusted (existing test/alt-layout path)."""
    runner = tmp_path / "runner.js"
    runner.write_text("")
    provider = WindowsAclProvider(platform="nt", node="C:/node.exe", runner=runner)
    usable, _ = provider.usable()
    assert usable is True
