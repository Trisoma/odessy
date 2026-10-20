"""What ``odessy doctor --voice`` reports, and what ``--setup-wake`` refuses to accept.

Doctor's probes are injected, so these tests describe the *decisions* rather than the
host: a missing model, an unpinned digest, and one device used for both capture and
playback are all reachable without a microphone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from odessy.config.doctor import Probes, run_checks, render
from odessy.voice.assets import WAKE_ASSETS, WakeAsset, WakeAssetError, install, manifest_path


def _probes(*, existing: set[str] = frozenset(), devices: bool = True) -> Probes:
    return Probes(
        which=lambda command: f"/usr/bin/{command}",
        http_get_json=lambda url, timeout: {},
        tcp_reachable=lambda host, port, timeout: True,
        command_output=lambda argv, timeout: "card 0: Headset [USB Audio]" if devices else "",
        node_tools_state=lambda: (True, True),
        path_exists=lambda path: str(path) in existing,
    )


def _sections(checks) -> set[str]:
    return {check.section for check in checks}


def test_wake_checks_are_only_reported_in_voice_mode() -> None:
    plain = run_checks(env={}, probes=_probes())
    voice = run_checks(env={}, probes=_probes(), voice=True)

    assert "Wake session (optional)" not in _sections(plain)
    assert "Wake session (optional)" in _sections(voice)


def test_a_missing_wake_model_names_the_setup_command() -> None:
    checks = [c for c in run_checks(env={}, probes=_probes(), voice=True) if c.section == "Wake session (optional)"]

    missing = [c for c in checks if "wake model not found" in c.summary]
    assert missing, "a missing model must be reported"
    assert missing[0].fix == "odessy voice --setup-wake"


def test_an_unpinned_model_is_reported_as_unverified(tmp_path: Path) -> None:
    model = tmp_path / "hey_jarvis_v0.1.onnx"
    model.write_bytes(b"model")
    manifest_path(tmp_path).write_text(
        json.dumps({"assets": [{"name": model.name, "verified": False}]}), encoding="utf-8"
    )

    checks = run_checks(
        env={"ODESSY_WAKE_MODEL": str(model)},
        probes=_probes(existing={str(model)}),
        voice=True,
    )

    assert any("digest is not pinned" in c.summary for c in checks)


def test_a_verified_model_is_reported_as_verified(tmp_path: Path) -> None:
    model = tmp_path / "hey_jarvis_v0.1.onnx"
    model.write_bytes(b"model")
    manifest_path(tmp_path).write_text(
        json.dumps({"assets": [{"name": model.name, "verified": True}]}), encoding="utf-8"
    )

    checks = run_checks(
        env={"ODESSY_WAKE_MODEL": str(model)},
        probes=_probes(existing={str(model)}),
        voice=True,
    )

    assert any("wake model verified" in c.summary for c in checks)


def test_no_capture_device_is_reported() -> None:
    checks = run_checks(env={}, probes=_probes(devices=False), voice=True)

    assert any("no capture device" in c.summary for c in checks)


def test_one_device_for_both_directions_is_reported() -> None:
    """The session listens while it speaks, so one device for both is the classic failure."""

    checks = run_checks(
        env={
            "ODESSY_AUDIO_INPUT_DEVICE": "plughw:0,0",
            "ODESSY_AUDIO_OUTPUT_DEVICE": "plughw:0,0",
        },
        probes=_probes(),
        voice=True,
    )

    assert any("same device" in c.summary for c in checks)


def test_render_keeps_wake_warnings_out_of_the_failure_count() -> None:
    text = render([c for c in run_checks(env={}, probes=_probes(), voice=True)])

    assert "wake" in text.lower()


# -- the installer ------------------------------------------------------------


def test_a_pinned_digest_is_verified_and_recorded(tmp_path: Path) -> None:
    payload = b"pretend model"
    digest = hashlib.sha256(payload).hexdigest()
    asset = WakeAsset(name="m.onnx", url="https://example.invalid/m.onnx",
                      license="CC-BY-NC-SA-4.0", version="v1", sha256=digest)

    lines = install(tmp_path, assets=(asset,), fetcher=lambda url: payload)

    assert (tmp_path / "m.onnx").read_bytes() == payload
    assert any("verified" in line for line in lines)
    manifest = json.loads(manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert manifest["assets"][0]["verified"] is True
    assert manifest["assets"][0]["license"] == "CC-BY-NC-SA-4.0"


def test_a_mismatched_digest_writes_nothing(tmp_path: Path) -> None:
    asset = WakeAsset(name="m.onnx", url="https://example.invalid/m.onnx",
                      license="CC-BY-NC-SA-4.0", version="v1", sha256="0" * 64)

    try:
        install(tmp_path, assets=(asset,), fetcher=lambda url: b"tampered")
    except WakeAssetError as error:
        assert "checksum mismatch" in str(error)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("a checksum mismatch must abort the install")

    assert not (tmp_path / "m.onnx").exists()


def test_an_unpinned_asset_is_reported_as_unverified_and_prints_its_digest(tmp_path: Path) -> None:
    asset = WakeAsset(name="m.onnx", url="https://example.invalid/m.onnx",
                      license="CC-BY-NC-SA-4.0", version="v1", sha256=None)

    lines = install(tmp_path, assets=(asset,), fetcher=lambda url: b"pretend model")

    assert any("unverified" in line for line in lines)
    assert any("pin this digest" in line for line in lines)


def test_the_default_manifest_digest_is_pinned() -> None:
    """The shipped asset is verified, not merely downloaded.

    The digest was computed from the release asset on 2026-10-20. If this test fails,
    the pinned value changed: re-fetch the asset, compare it against the recorded
    licence and version, and update the expectation deliberately rather than by
    pasting the new digest.
    """

    unpinned = [asset.name for asset in WAKE_ASSETS if asset.sha256 is None]

    assert unpinned == []
