"""Prompt 08: the policy translation, and the pin it is written against.

These tests prove the *translation*: what the pinned runtime is asked for, what stays with
an Odessy provider, what a refusal looks like, and that no path silently widens authority.
They deliberately do **not** prove that an operating system enforces anything — that is
``tests/test_enforcement_probe.py``, which runs real commands.

The pin's vocabulary is quoted in the module docstring of
``odessy.runtime_backends.harness.sandbox`` and measured by
``experiments/restructure/harness_compatibility/probe_sandbox_vocabulary.py``. The first
test here pins those names so a pin change cannot quietly pass review.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from odessy.config.sandbox import SandboxSettings
from odessy.execution.contract import ExecutionPolicy
from odessy.runtime_backends import RuntimeUnavailable
from odessy.runtime_backends.harness import NARROW_PATCH_NAME
from odessy.runtime_backends.harness.approvals import ApprovalBindError, HarnessApprovalBridge
from odessy.runtime_backends.harness.config import (
    HarnessConfig,
    assert_extras_leave_tools_alone,
    from_environment,
)
from odessy.runtime_backends.harness.profile import materialize_narrow_patch
from odessy.runtime_backends.harness.sandbox import (
    HARNESS_CARRIES,
    HARNESS_LACKS,
    PIN,
    SANDBOX_PATCH_PREFIX,
    SandboxRequirementRefused,
    explicit_plan,
    materialize_sandbox_patch,
    plan_for_session,
    translate,
)

CANNOT = ("cannot_restrict_network", "cannot_hide_paths", "cannot_restrict_reads")

ALLOW_ONCE = {"optionId": "a", "kind": "allow_once"}
REJECT_ONCE = {"optionId": "r", "kind": "reject_once"}


# -- the vocabulary this module is written against ---------------------------

def test_the_pinned_vocabulary_is_named_and_narrow():
    assert HARNESS_CARRIES == ("mode", "workspace_root")
    assert set(HARNESS_LACKS) == {"network", "hidden_paths", "extra_writable", "per_call_mode"}
    assert "0.2.1rc2" in PIN


# -- modes -------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["read-only", "workspace-write", "danger-full-access"])
def test_a_mode_translates_unchanged(tmp_path, mode):
    plan = translate(workspace=tmp_path, mode=mode, network="allow", provider="bwrap",
                     enforcement="full")
    assert plan.mode == mode
    assert plan.workspace_root == str(tmp_path.resolve())


def test_an_unknown_mode_is_a_caller_error_not_a_default(tmp_path):
    with pytest.raises(ValueError):
        translate(workspace=tmp_path, mode="sandbox-ish", network="allow")  # type: ignore[arg-type]


def test_the_workspace_root_is_absolute_even_when_the_caller_passes_a_relative_path():
    plan = translate(workspace=".", mode="workspace-write", network="allow")
    assert Path(plan.workspace_root).is_absolute()


# -- network denial ----------------------------------------------------------

def test_a_host_that_cannot_cut_the_network_says_so_instead_of_pretending(tmp_path):
    plan = translate(workspace=tmp_path, mode="workspace-write", network="deny",
                     limitations=CANNOT, provider="windows-acl", enforcement="partial")
    assert "network" in plan.unenforced
    assert plan.dimension("network").enforced is False
    assert plan.launchable  # preferred isolation: run, and state the gap


def test_required_isolation_refuses_a_network_denial_the_host_cannot_make(tmp_path):
    with pytest.raises(SandboxRequirementRefused) as caught:
        translate(workspace=tmp_path, mode="workspace-write", network="deny",
                  limitations=CANNOT, provider="windows-acl", enforcement="partial",
                  isolation="required")
    message = str(caught.value)
    assert "network" in message
    assert "required" in message


def test_a_backend_that_can_cut_the_network_is_believed_only_for_what_it_reports(tmp_path):
    plan = translate(workspace=tmp_path, mode="workspace-write", network="deny",
                     limitations=("cannot_restrict_reads",), provider="bwrap", enforcement="full")
    assert plan.dimension("network").enforced is True
    assert plan.provider == "bwrap"
    assert plan.unenforced == ()


# -- hidden paths are not "reads" --------------------------------------------

def test_unrestricted_reads_and_hidden_paths_are_separate_statements(tmp_path):
    hidden = tmp_path / "secrets"
    plan = translate(workspace=tmp_path, mode="workspace-write", network="allow",
                     hidden_paths=[hidden], limitations=CANNOT, provider="windows-acl",
                     enforcement="partial")
    # The backend reads the whole host *and* hides nothing; both are reported, and neither
    # is inferred from the other.
    assert plan.reads == "unrestricted"
    assert plan.dimension("hidden_paths").requested is True
    assert plan.dimension("hidden_paths").enforced is False
    assert "cannot_hide_paths" in plan.dimension("hidden_paths").evidence


def test_a_backend_that_masks_paths_is_credited_even_though_reads_are_open(tmp_path):
    plan = translate(workspace=tmp_path, mode="workspace-write", network="allow",
                     hidden_paths=[tmp_path / "secrets"],
                     limitations=("cannot_restrict_reads",), provider="bwrap", enforcement="full")
    assert plan.reads == "unrestricted"
    assert plan.dimension("hidden_paths").enforced is True


def test_a_supported_dimension_is_unavailable_at_the_harness_boundary(tmp_path):
    """A host provider's capability is not enforcement at a boundary that cannot apply it.

    bwrap can cut the network and mask a path; the pinned runtime has no vocabulary for
    either and nothing on the launch path wraps a tool with bwrap. Recording that as
    "enforced" would credit a boundary that does not exist.
    """

    plan = translate(workspace=tmp_path, mode="workspace-write", network="deny",
                     hidden_paths=[tmp_path / "secrets"],
                     limitations=("cannot_restrict_reads",), provider="bwrap",
                     enforcement="full", boundary="harness")
    assert plan.boundary == "harness"
    assert plan.dimension("network").enforced is False
    assert plan.dimension("hidden_paths").enforced is False
    assert "nothing applies it at this boundary" in plan.dimension("network").evidence
    assert set(plan.unenforced) == {"network", "hidden_paths"}


def test_required_isolation_refuses_at_the_harness_boundary_even_with_a_capable_host(tmp_path):
    with pytest.raises(SandboxRequirementRefused):
        translate(workspace=tmp_path, mode="workspace-write", network="deny",
                  hidden_paths=[tmp_path / "secrets"], limitations=("cannot_restrict_reads",),
                  provider="bwrap", enforcement="full", isolation="required",
                  boundary="harness")


def test_the_session_plan_is_a_harness_boundary_plan(tmp_path):
    plan = plan_for_session(workspace=tmp_path, mode="workspace-write",
                            settings=SandboxSettings(), environ={})
    assert plan.boundary == "harness"
    # The default hidden list is requested and cannot be masked by this boundary.
    assert "hidden_paths" in plan.unenforced


def test_hiding_a_path_cannot_be_claimed_by_a_backend_that_does_not_name_it(tmp_path):
    plan = translate(workspace=tmp_path, mode="workspace-write", network="allow",
                     hidden_paths=[tmp_path / "secrets"],
                     limitations=("cannot_hide_paths",), provider="bwrap", enforcement="full")
    assert plan.dimension("hidden_paths").enforced is False


def test_no_hidden_path_means_the_dimension_is_not_requested(tmp_path):
    plan = translate(workspace=tmp_path, mode="workspace-write", network="allow",
                     limitations=CANNOT, provider="windows-acl", enforcement="partial")
    assert plan.dimension("hidden_paths").requested is False
    assert "hidden_paths" not in plan.unenforced


# -- extra writable roots ----------------------------------------------------

def test_extra_writable_never_blocks_a_launch_because_it_only_adds(tmp_path):
    plan = translate(workspace=tmp_path, mode="workspace-write", network="allow",
                     extra_writable=[tmp_path / "cache"], limitations=CANNOT,
                     provider="windows-acl", enforcement="partial", isolation="required")
    dimension = plan.dimension("extra_writable")
    assert dimension.requested is True and dimension.harness is False
    assert "not granted" in dimension.evidence
    # A cache folder that is not granted makes the command fail; it never widens anything.
    assert "extra_writable" not in plan.unenforced


# -- escalation --------------------------------------------------------------

def test_required_isolation_cannot_be_met_by_a_full_access_request(tmp_path):
    with pytest.raises(SandboxRequirementRefused):
        translate(workspace=tmp_path, mode="danger-full-access", network="allow",
                  isolation="required", provider="unconfined")
    plan = translate(workspace=tmp_path, mode="danger-full-access", network="allow",
                     provider="unconfined", enforcement="none")
    assert plan.launchable and plan.unenforced == ()


def _bridge(tmp_path: Path, *, escalation: str, mode: str = "workspace-write") -> HarnessApprovalBridge:
    return HarnessApprovalBridge(
        workspace=tmp_path,
        identity=lambda: {"version": "0.2.1rc2", "artifact": "sha", "odessy_session": "s1"},
        approver=lambda details: (True, "resolved"),
        escalation=escalation,
        sandbox_mode=mode,
    )


def _observe_call(bridge: HarnessApprovalBridge, *, call_id: str, arguments: dict) -> None:
    bridge.begin_turn(1, session_id="acp-1")
    bridge.observe({
        "method": "session/update",
        "params": {"sessionId": "acp-1", "update": {
            "sessionUpdate": "tool_call", "toolCallId": call_id,
            "title": "tool-pwsh", "rawInput": arguments,
        }},
    })


def _permission(call_id: str) -> dict:
    return {"sessionId": "acp-1", "toolCall": {"toolCallId": call_id},
            "options": [ALLOW_ONCE, REJECT_ONCE]}


def test_a_request_at_the_session_mode_is_an_ordinary_approval(tmp_path):
    bridge = _bridge(tmp_path, escalation="never", mode="workspace-write")
    _observe_call(bridge, call_id="c1", arguments={"command": "echo hi",
                                                  "sandbox_permissions": "workspace-write"})
    bound = bridge.bind(1, _permission("c1"))
    assert bound.allow_option == "a"
    assert bridge.stats.escalation_refused == 0


def test_escalation_never_refuses_a_widening_request_without_asking(tmp_path):
    bridge = _bridge(tmp_path, escalation="never", mode="read-only")
    _observe_call(bridge, call_id="c2", arguments={"command": "rm -rf x",
                                                  "sandbox_permissions": "workspace-write"})
    with pytest.raises(ApprovalBindError) as caught:
        bridge.bind(1, _permission("c2"))
    assert "'never'" in str(caught.value)
    assert bridge.stats.escalation_refused == 1
    assert bridge.stats.bound == 0


def test_a_widening_request_is_refused_even_when_the_setting_allows_asking(tmp_path):
    bridge = _bridge(tmp_path, escalation="ask", mode="workspace-write")
    _observe_call(bridge, call_id="c3", arguments={"command": "x",
                                                  "sandbox_permissions": "danger-full-access"})
    with pytest.raises(ApprovalBindError) as caught:
        bridge.bind(1, _permission("c3"))
    # The pin resolves the sandbox mode once per process, so an approval could not be
    # honoured; promising one would be a false statement about the boundary.
    assert "cannot be carried" in str(caught.value)
    assert bridge.stats.escalation_refused == 1


def test_a_widening_request_with_no_rankable_target_is_refused(tmp_path):
    bridge = _bridge(tmp_path, escalation="ask", mode="workspace-write")
    _observe_call(bridge, call_id="c4", arguments={"sandbox_permissions": "god-mode"})
    with pytest.raises(ApprovalBindError) as caught:
        bridge.bind(1, _permission("c4"))
    assert "unknown sandbox mode" in str(caught.value)
    assert bridge.stats.escalation_refused == 1


def test_an_ordinary_request_is_still_bound_after_the_escalation_checks(tmp_path):
    bridge = _bridge(tmp_path, escalation="never")
    _observe_call(bridge, call_id="c5", arguments={"command": "echo hi"})
    bound = bridge.bind(1, _permission("c5"))
    assert bound.allow_option == "a" and bridge.stats.bound == 1


# -- the launch --------------------------------------------------------------

def _config(tmp_path: Path, plan=None) -> HarnessConfig:
    home = tmp_path / "home"
    return HarnessConfig(command=(sys.executable,), home=home, workspace=tmp_path,
                         narrow_patch=materialize_narrow_patch(home), sandbox=plan)


def test_the_child_environment_pins_the_mode_and_cannot_be_overridden(tmp_path):
    config = _config(tmp_path, explicit_plan(tmp_path, mode="read-only"))
    env = config.child_env({"SystemRoot": "C:/Windows"})
    assert env["DSH_PERMISSION_MODE"] == "read-only"
    # Even an operator passthrough cannot decide the confinement.
    passthrough = HarnessConfig(
        command=(sys.executable,), home=tmp_path / "home", workspace=tmp_path,
        narrow_patch=materialize_narrow_patch(tmp_path / "home"),
        sandbox=explicit_plan(tmp_path, mode="read-only"),
        passthrough=("DSH_PERMISSION_MODE",), extra_env={"DSH_PERMISSION_MODE": "danger-full-access"},
    )
    assert passthrough.child_env({"DSH_PERMISSION_MODE": "danger-full-access"})[
        "DSH_PERMISSION_MODE"] == "read-only"


def test_the_overlay_pins_the_two_fields_the_pin_accepts(tmp_path):
    plan = explicit_plan(tmp_path, mode="read-only")
    text = plan.patch_text()
    assert "- id: sandbox-policy" in text
    assert f"mode: '{plan.mode}'" in text
    assert f"workspaceRoot: '{plan.workspace_root}'" in text
    assert "- id: approval" in text and "policy: 'ask'" in text
    # It names no tool row: the narrow overlay still owns the tool surface.
    assert "tool-" not in text and "mcp-" not in text


def test_the_sandbox_overlay_is_written_into_the_harness_home(tmp_path):
    environ = {"ODESSY_HARNESS_EXE": sys.executable,
               "ODESSY_HARNESS_HOME": str(tmp_path / "home")}
    config = from_environment(environ, workspace=tmp_path)
    assert config.sandbox_patch is not None
    assert config.sandbox_patch.parent == config.home
    assert config.sandbox_patch.name.startswith(SANDBOX_PATCH_PREFIX)
    assert config.sandbox_patch.read_text(encoding="utf-8") == config.sandbox_plan().patch_text()
    assert config.ordered_patches()[-1] == config.sandbox_patch


def test_two_plans_get_distinct_overlays_and_no_shared_temporary(tmp_path):
    home = tmp_path / "home"
    first = materialize_sandbox_patch(home, explicit_plan(tmp_path, mode="read-only"))
    second = materialize_sandbox_patch(home, explicit_plan(tmp_path, mode="workspace-write"))
    assert first != second
    # Content-addressed, so re-materializing the same plan is stable and safe to race.
    again = materialize_sandbox_patch(home, explicit_plan(tmp_path, mode="read-only"))
    assert again == first
    assert "'read-only'" in first.read_text(encoding="utf-8")
    assert "'workspace-write'" in second.read_text(encoding="utf-8")
    # No `.tmp` sibling is left behind, and the write never targets a fixed name.
    assert not [path for path in home.iterdir() if path.suffix == ".tmp"]


def test_an_extra_overlay_cannot_widen_the_confinement(tmp_path):
    for row in ("sandbox-policy", "sandbox", "approval", "permission", "fs-sandbox"):
        overlay = tmp_path / f"{row}.yml"
        overlay.write_text(f"- id: {row}\n  config: {{}}\n", encoding="utf-8")
        with pytest.raises(RuntimeUnavailable) as caught:
            assert_extras_leave_tools_alone([overlay])
        assert row in caught.value.public()["detail"]


def test_a_plan_for_another_workspace_is_refused_at_validation(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    config = _config(tmp_path, explicit_plan(other))
    with pytest.raises(RuntimeUnavailable) as caught:
        config.validate()
    assert "workspace root" in caught.value.public()["reason"]


def test_required_isolation_either_launches_or_refuses_never_downgrades(tmp_path):
    """``from_environment`` must not return a plan that cannot honour ``required``.

    Which of the two outcomes happens is a property of the host, so the test asserts the
    invariant rather than one branch: a ``required`` policy either produces a launchable
    plan or fails with the dimension this host cannot enforce named.
    """

    environ = {"ODESSY_HARNESS_EXE": sys.executable,
               "ODESSY_HARNESS_HOME": str(tmp_path / "home"),
               "ODESSY_ISOLATION": "required"}
    try:
        config = from_environment(environ, workspace=tmp_path)
    except SandboxRequirementRefused as refused:
        assert refused.plan.unenforced
        assert str(refused)
    else:
        assert config.sandbox_plan().launchable
        assert config.sandbox_plan().isolation == "required"


# -- what this host resolves to ---------------------------------------------

def test_a_session_plan_names_the_host_backend_and_what_it_cannot_do(tmp_path):
    plan = plan_for_session(workspace=tmp_path, mode="workspace-write",
                            settings=SandboxSettings(), environ={})
    assert plan.provider is not None
    assert set(plan.unenforced) <= {"network", "hidden_paths"}
    # The default hidden list is non-empty, so a backend that cannot mask paths must say so.
    assert plan.dimension("hidden_paths").requested is True


def test_a_target_this_side_cannot_probe_is_assumed_unable():
    plan = plan_for_session(workspace=Path.cwd(), mode="workspace-write", target="laptop",
                            settings=SandboxSettings(), environ={})
    assert plan.target == "laptop"
    assert plan.provider is None
    # Nothing was probed there, so neither the mode itself nor the hidden paths are claimed.
    assert set(plan.unenforced) == {"mode", "hidden_paths"}


def test_no_hidden_paths_and_network_allowed_leave_nothing_unenforced(tmp_path):
    settings = SandboxSettings(hidden_paths=(), extra_writable=(), network_read_only="allow",
                               network_workspace_write="allow")
    plan = plan_for_session(workspace=tmp_path, settings=settings, mode="workspace-write",
                            environ={})
    assert plan.unenforced == ()


def test_the_plan_and_the_execution_layer_share_one_policy(tmp_path):
    plan = explicit_plan(tmp_path, mode="workspace-write", network="deny",
                         hidden_paths=[tmp_path / "secrets"], extra_writable=[tmp_path / "cache"])
    policy = plan.execution_policy()
    assert isinstance(policy, ExecutionPolicy)
    assert policy.workspace_root == plan.workspace_root
    arguments = plan.execution_arguments()
    assert arguments["network"] == "deny"
    assert arguments["hidden_paths"] == list(plan.hidden_paths)
    assert arguments["extra_writable"] == list(plan.extra_writable)


def test_the_narrow_overlay_name_is_still_the_reviewed_one(tmp_path):
    # Guards against a rename in profile.py drifting away from the config's validation.
    assert NARROW_PATCH_NAME == "odessy_narrow.patch.yml"
