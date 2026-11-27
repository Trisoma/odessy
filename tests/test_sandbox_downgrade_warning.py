"""A fail-open run states the downgrade where the user sees the result.

The default (auto -> preferred with no backend) is deliberately unchanged. What is
new is that a command result with enforcement=none carries a plain-language warning
and the remedy, so a reader who never opens the approval card or doctor still learns
the command was unconstrained.
"""

from odessy.authority.command_bridge import _sandbox_report
from odessy.execution.contract import ConfineResult, ExecutionPolicy


def _result(enforcement: str, provider_id: str) -> ConfineResult:
    return ConfineResult(
        argv=("cmd",),
        enforcement=enforcement,  # type: ignore[arg-type]
        limitations=frozenset(),
        denial_signatures=(),
        runner_failure_rules=(),
        provider_id=provider_id,
    )


def test_unconfined_result_carries_warning_and_remedy():
    report = _sandbox_report(_result("none", "unconfined"))
    assert report["enforcement"] == "none"
    assert "NO SANDBOX" in report["warning"]
    assert "ODESSY_ISOLATION=required" in report["warning"]
    assert "odessy doctor" in report["warning"]


def test_confined_result_has_no_warning():
    for enforcement, provider in (("full", "bwrap"), ("partial", "windows-acl")):
        report = _sandbox_report(_result(enforcement, provider),
                                 ExecutionPolicy(mode="read-only", workspace_root="/w"))
        assert "warning" not in report


def test_no_policy_unconfined_result_still_warns():
    report = _sandbox_report(_result("none", "unconfined"))
    assert "NO SANDBOX" in report["warning"]
