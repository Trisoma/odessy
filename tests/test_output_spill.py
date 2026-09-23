"""Output retention, spill, retrieval, and redaction tests.

Covers the pipeline that replaced head-only truncation:

```text
redact -> head+tail retain -> spill full text -> locator + notice
```

The storage-failure and boundary cases matter most: those are the paths where a
mistake turns bounded output into silently lost output.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from odessy.command_bridge import CommandBridge, TerminalRunResult
from odessy.output_retention import TextRetainer, format_retention_notice
from odessy.redaction import REDACTED, looks_like_secret_name, redact_text
from odessy.spill import SpillLocator, SpillStore
from odessy.workspace_authority import WorkspaceAuthorityError


def build_bridge(workspace: Path, **kwargs) -> CommandBridge:
    return CommandBridge(workspace_root=workspace, **kwargs)


def build_store(workspace: Path) -> SpillStore:
    return SpillStore(workspace_root=workspace, session_id="test")


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------


def test_small_text_is_returned_unchanged() -> None:
    result = TextRetainer(head_bytes=100, tail_bytes=100).retain("hello world")

    assert result.text == "hello world"
    assert result.truncated is False
    assert result.omitted_bytes.count == 0


def test_head_and_tail_retention_keeps_both_ends() -> None:
    text = "HEAD" + ("x" * 500) + "TAIL"

    result = TextRetainer(head_bytes=20, tail_bytes=20).retain(text)

    assert result.truncated is True
    assert result.text.startswith("HEAD")
    assert result.text.endswith("TAIL")
    assert "x" * 500 not in result.text


def test_omitted_count_is_exact_and_measured_after_trimming() -> None:
    """The count must describe what was actually kept, not the budget."""

    text = "a" * 1000

    result = TextRetainer(head_bytes=10, tail_bytes=10).retain(text)

    assert result.kept_prefix_bytes == 10
    assert result.kept_suffix_bytes == 10
    assert result.omitted_bytes.count == 1000 - 20
    assert result.total_bytes == 1000
    assert result.kept_prefix_bytes + result.kept_suffix_bytes + result.omitted_bytes.count == 1000


def test_utf8_boundary_never_splits_a_codepoint() -> None:
    """A cut through a multi-byte sequence must not corrupt the text."""

    # 3-byte codepoints either side of a deliberately misaligned budget.
    text = "€" * 100

    result = TextRetainer(head_bytes=7, tail_bytes=7).retain(text)

    # Decoding would have raised earlier if a partial sequence survived.
    assert result.truncated is True
    # Two codepoints kept per side: 7 bytes holds two 3-byte codepoints and
    # drops the trailing partial byte.
    assert result.text.count("€") == 4
    assert result.kept_prefix_bytes == 6
    assert result.kept_suffix_bytes == 6
    assert result.omitted_bytes.count == 300 - 12


def test_utf8_boundary_survives_four_byte_codepoints() -> None:
    text = "😀" * 50

    result = TextRetainer(head_bytes=5, tail_bytes=5).retain(text)

    assert result.kept_prefix_bytes == 4
    assert result.kept_suffix_bytes == 4
    assert result.omitted_bytes.count == 200 - 8
    assert result.text.count("😀") == 2


@pytest.mark.parametrize(
    "head,tail",
    [(0, 0), (0, 10), (10, 0), (1, 1), (0, 1000), (1000, 1000)],
)
def test_tiny_and_zero_budgets_are_safe(head: int, tail: int) -> None:
    text = "content that is longer than the budget" * 4

    result = TextRetainer(head_bytes=head, tail_bytes=tail).retain(text)

    assert result.omitted_bytes.count >= 0
    assert result.kept_prefix_bytes + result.kept_suffix_bytes + result.omitted_bytes.count == (
        result.total_bytes
    )
    assert isinstance(result.text, str)


def test_overlapping_windows_return_original_rather_than_negative_omission() -> None:
    result = TextRetainer(head_bytes=1000, tail_bytes=1000).retain("short")

    assert result.truncated is False
    assert result.omitted_bytes.count == 0


def test_notice_is_silent_when_nothing_was_omitted() -> None:
    result = TextRetainer(head_bytes=100, tail_bytes=100).retain("small")

    assert format_retention_notice(result) == ""


def test_notice_reports_exact_omission_and_recovery() -> None:
    result = TextRetainer(head_bytes=10, tail_bytes=10).retain("a" * 100)

    notice = format_retention_notice(
        result, unit_label="stdout", recovery="Full output: .odessy/spill/x.txt"
    )

    assert "omitted 80 bytes of 100" in notice
    assert "stdout" in notice
    assert ".odessy/spill/x.txt" in notice


# --------------------------------------------------------------------------
# Spill store
# --------------------------------------------------------------------------


def test_spill_round_trip_returns_byte_identical_text(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    payload = "line one\nline two\n" + ("unicode é 😀\n" * 50)

    locator = store.save_text(payload, label="stdout")

    assert locator is not None
    assert locator.byte_count == len(payload.encode("utf-8"))
    assert locator.line_count == len(payload.splitlines())
    assert len(locator.digest) == 16
    assert store.read_text(locator) == payload


def test_spill_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = build_store(tmp_path)

    first = store.save_text("same bytes", label="stdout")
    second = store.save_text("same bytes", label="stdout")

    assert first is not None and second is not None
    assert first.path == second.path


def test_spill_label_cannot_escape_the_spill_directory(tmp_path: Path) -> None:
    store = build_store(tmp_path)

    locator = store.save_text("payload", label="../../etc/passwd")

    assert locator is not None
    resolved = Path(locator.path).resolve()
    assert resolved.parent == store.directory.resolve()
    assert ".." not in locator.path


def test_spill_session_cannot_escape_the_spill_root(tmp_path: Path) -> None:
    store = SpillStore(workspace_root=tmp_path, session_id="../../../escape")

    assert (tmp_path / ".odessy" / "spill").resolve() in store.directory.resolve().parents


def test_relative_locator_path_is_workspace_scoped(tmp_path: Path) -> None:
    store = build_store(tmp_path)

    locator = store.save_text("payload", label="stdout")

    assert locator is not None
    assert locator.relative_path.startswith(".odessy/spill/")
    assert not Path(locator.relative_path).is_absolute()


def test_storage_failure_returns_none_instead_of_raising(tmp_path: Path) -> None:
    """A file where the spill root should be forces the failure branch."""

    (tmp_path / ".odessy").write_text("not a directory", encoding="utf-8")
    store = build_store(tmp_path)

    assert store.save_text("payload", label="stdout") is None


def test_read_text_rejects_a_locator_outside_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    store = build_store(workspace)

    forged = SpillLocator(
        path=str(outside),
        relative_path="outside.txt",
        byte_count=6,
        line_count=1,
        digest="0" * 16,
    )

    with pytest.raises(WorkspaceAuthorityError):
        store.read_text(forged)


# --------------------------------------------------------------------------
# CommandBridge integration
# --------------------------------------------------------------------------


def test_small_output_stays_inline_without_a_spill(tmp_path: Path) -> None:
    bridge = build_bridge(tmp_path, spill_store=build_store(tmp_path))

    text, truncated, locator = bridge._prepare_output(
        "hello", label="stdout", max_chars=1000
    )

    assert (text, truncated, locator) == ("hello", False, None)


def test_large_output_spills_and_reports_an_exact_notice(tmp_path: Path) -> None:
    bridge = build_bridge(tmp_path, spill_store=build_store(tmp_path))
    payload = "HEAD" + ("x" * 50_000) + "TAIL"

    text, truncated, locator = bridge._prepare_output(
        payload, label="stdout", max_chars=1000
    )

    assert truncated is True
    assert locator is not None
    assert text.startswith("HEAD")
    assert text.endswith("TAIL") is False  # notice is appended after the tail
    assert "TAIL" in text
    assert "omitted" in text
    assert locator["relative_path"] in text
    # The spilled copy is the full original, not the preview.
    recovered = build_store(tmp_path).read_text(SpillLocator(**locator))
    assert recovered == payload


def test_storage_failure_preserves_the_original_content(tmp_path: Path) -> None:
    """Never degrade to lossy truncation: keep everything."""

    (tmp_path / ".odessy").write_text("not a directory", encoding="utf-8")
    bridge = build_bridge(tmp_path)
    payload = "x" * 50_000

    text, truncated, locator = bridge._prepare_output(
        payload, label="stdout", max_chars=1000
    )

    assert locator is None
    assert truncated is False
    assert text == payload


def test_metadata_is_untouched_by_a_real_large_run(tmp_path: Path) -> None:
    """Only the text fields shrink; every other key survives byte-for-byte.

    A script file is used rather than `-c` so the test does not depend on shell
    quoting rules, which differ between the Windows and POSIX runners.
    """

    emitter = tmp_path / "emit.py"
    emitter.write_text("print('y' * 200000)\n", encoding="utf-8")
    bridge = build_bridge(tmp_path)
    # PowerShell needs the call operator to run a quoted executable path.
    if os.name == "nt":
        command = f'& "{sys.executable}" emit.py'
    else:
        command = f'"{sys.executable}" emit.py'

    result = bridge.run(command=command, cwd=".", timeout=60)

    assert result["exit_code"] == 0
    assert result["status"] == "ok"
    assert result["timed_out"] is False
    assert result["command"] == command
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is False
    assert isinstance(result["duration"], float)
    assert result["stdout_spill"] is not None
    # Existing consumers still see both legacy fields.
    assert set(TerminalRunResult.__annotations__) <= set(result)


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


def test_secret_assignment_is_redacted_but_the_name_survives() -> None:
    redacted = redact_text("API_KEY=sk-abcdef1234567890")

    assert "sk-abcdef1234567890" not in redacted
    assert "API_KEY" in redacted
    assert REDACTED in redacted


@pytest.mark.parametrize(
    "line",
    [
        "OPENAI_API_KEY=abcdefghijklmnop",
        "AWS_SECRET_ACCESS_KEY=abcdefghijklmnop",
        '"token": "abcdefghijklmnop"',
        "PASSWORD=abcdefghijklmnop",
        "GITHUB_TOKEN=ghp_abcdefghijklmnopqrst",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_representative_secrets_are_redacted(line: str) -> None:
    assert "abcdefghijklmnop" not in redact_text(line)


@pytest.mark.parametrize(
    "line",
    ["PATH=/usr/local/bin", "DEBUG=on", "TOKEN=1", "COUNT=42", "hello world"],
)
def test_innocuous_text_is_left_alone(line: str) -> None:
    assert redact_text(line) == line


def test_redaction_is_idempotent() -> None:
    once = redact_text("API_KEY=abcdefghijklmnop")

    assert redact_text(once) == once


def test_secret_name_vocabulary_is_shared_with_environment_screening() -> None:
    assert looks_like_secret_name("MY_APP_API_KEY") is True
    assert looks_like_secret_name("PATH") is False


def test_secret_absent_from_observation_spill_and_debug_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three leak surfaces named in the design doc are all covered."""

    secret = "sk-abcdef1234567890abcdef"
    bridge = build_bridge(tmp_path)
    payload = f"API_KEY={secret}\n" + ("filler line\n" * 5000)

    text, _, locator = bridge._prepare_output(payload, label="stdout", max_chars=500)

    # 1. Model-facing observation.
    assert secret not in text
    # 2. Spilled file on disk.
    assert locator is not None
    spilled = Path(locator["path"]).read_text(encoding="utf-8")
    assert secret not in spilled
    assert REDACTED in spilled
    # 3. Debug log.
    from odessy.ollama_model import _debug_model_repair

    _debug_model_repair([("raw output", payload)])
    assert secret not in capsys.readouterr().out
