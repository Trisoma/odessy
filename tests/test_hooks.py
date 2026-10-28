"""Hooks (hooks.toml): configuration, the hook protocol, and where the agent runs them."""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from odessy.authority.policy import OdessyPolicy
from odessy.config.doctor import _hooks_checks
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.hooks import (
    Hook,
    HookConfigError,
    HookRun,
    HookSet,
    HookVerdict,
    load_hooks,
    public_event,
    run_hook,
    tighten,
)
from odessy.runtime.types import FinalResponse, LogEntry, PolicyDecision, ToolBatch, ToolCall, ToolRequest
from odessy.tools.mcp_server import build_odessy_mcp_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# A hook script: appends the JSON it reads to record.jsonl, then answers as told by
# its first argument.
HOOK_SCRIPT = r'''
import json, os, sys, time
payload = json.load(sys.stdin)
payload["env"] = sorted(os.environ)
with open(os.path.join(os.path.dirname(__file__), "record.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(payload) + "\n")
mode = sys.argv[1]
if mode == "allow":
    pass
elif mode == "deny":
    print("force push is not allowed here", file=sys.stderr)
    sys.exit(2)
elif mode == "confirm":
    print(json.dumps({"decision": "confirm", "reason": "outside office hours"}))
elif mode == "garbage":
    print("not json")
elif mode == "sleep":
    time.sleep(10)
elif mode == "note":
    print("lint: 0 problems")
elif mode == "fail-note":
    print("lint: 2 problems")
    sys.exit(1)
'''


def hook(tmp_path: Path, event: str, mode: str, **fields) -> Hook:
    script = tmp_path / "hook.py"
    script.write_text(HOOK_SCRIPT, encoding="utf-8")
    return Hook(event=event, name=fields.pop("name", mode), command=(sys.executable, str(script), mode), **fields)


def records(tmp_path: Path) -> list[dict]:
    path = tmp_path / "record.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class Scripted:
    """A model that returns the given actions in order."""

    def __init__(self, *actions):
        self.actions = list(actions)
        self.observations = []

    def propose(self, user_text):
        return self.actions.pop(0)

    def respond_to_observation(self, user_text, observation):
        self.observations.append(observation)
        return self.actions.pop(0)

    def respond_to_observations(self, user_text, observations):
        self.observations.extend(observations)
        return self.actions.pop(0)


def agent(workspace: Path, model, hooks, confirm=False) -> OdessyAgent:
    return OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=workspace),
        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace)),
        confirm=confirm,
        hooks=HookSet(tuple(hooks), workspace) if hooks is not None else None,
    )


PWD = ToolRequest("terminal.run", {"command": "pwd", "timeout": 30})


# --- configuration -------------------------------------------------------------


def test_load_hooks_reads_each_event_and_limits_hooks_to_their_workspace(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    path = tmp_path / "hooks.toml"
    path.write_text(
        f'''
[[pre_tool]]
name = "guard"
match = "terminal.*"
risk = ["medium", "high"]
command = ["python", "guard.py"]
on_error = "confirm"

[[post_tool]]
name = "lint"
match = ["filesystem.write", "filesystem.edit"]
workspace = "{other.as_posix()}"
command = ["ruff", "check"]
timeout = 20

[[turn_end]]
name = "notify"
command = ["notify-send", "done"]
''',
        encoding="utf-8",
    )
    hooks = load_hooks(tmp_path, path)
    assert [h.name for h in hooks.hooks] == ["guard", "notify"]
    guard = hooks.for_event("pre_tool")[0]
    assert guard.on_error == "confirm" and guard.timeout == 5.0
    assert guard.applies("terminal.run", "high") and not guard.applies("terminal.run", "low")
    assert not guard.applies("filesystem.read", "high")
    assert [h.name for h in load_hooks(other, path).hooks] == ["guard", "lint", "notify"]


def test_no_file_means_no_hooks(tmp_path):
    assert load_hooks(tmp_path, tmp_path / "missing.toml") is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[[before]]\nname = "x"\ncommand = ["a"]', "unknown keys"),
        ('[[pre_tool]]\nname = "x"\ncommand = ["a"]\non_error = "allow"', "never allows"),
        ('[[pre_tool]]\nname = "x"\ncommand = "a b"', "list of strings"),
        ('[[pre_tool]]\nname = "Bad Name"\ncommand = ["a"]', "name must"),
        ('[[turn_end]]\nname = "x"\ncommand = ["a"]\nmatch = "*"', "unknown keys"),
        ('[[post_tool]]\nname = "x"\ncommand = ["a"]\ntimeout = 0', "timeout"),
        ('[[pre_tool]]\nname = "x"\ncommand = ["a"]\n[[post_tool]]\nname = "x"\ncommand = ["b"]', "two hooks"),
    ],
)
def test_malformed_hooks_files_are_rejected(tmp_path, body, message):
    path = tmp_path / "hooks.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(HookConfigError, match=message):
        load_hooks(tmp_path, path)


def test_doctor_lists_hooks_and_reports_a_bad_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "hooks.toml"
    path.write_text(f'[[pre_tool]]\nname = "guard"\ncommand = ["{Path(sys.executable).as_posix()}"]\n', encoding="utf-8")
    (check,) = _hooks_checks({"ODESSY_HOOKS_FILE": str(path)})
    assert check.status == "ok" and "pre_tool: guard" in check.summary and "outside the sandbox" in check.summary
    path.write_text('[[pre_tool]]\nname = "guard"\ncommand = ["no-such-program-odessy"]\n', encoding="utf-8")
    (check,) = _hooks_checks({"ODESSY_HOOKS_FILE": str(path)})
    assert check.status == "warn" and "no-such-program-odessy" in check.summary
    path.write_text("[[pre_tool]]\n", encoding="utf-8")
    (check,) = _hooks_checks({"ODESSY_HOOKS_FILE": str(path)})
    assert check.status == "fail"
    assert _hooks_checks({"ODESSY_HOOKS_FILE": str(tmp_path / "none.toml")}) == []


# --- decisions -----------------------------------------------------------------


def test_tighten_only_makes_a_decision_stricter():
    allow = PolicyDecision("allow", "read-only command", "low")
    deny = PolicyDecision("deny", "outside the workspace", "high")
    confirm = PolicyDecision("confirm", "writes files", "medium", preapproved=True)

    assert tighten(allow, HookVerdict("allow", "", "guard")) == allow
    assert tighten(deny, HookVerdict("allow", "", "guard")) == deny
    assert tighten(deny, HookVerdict("confirm", "", "guard")) == deny

    denied = tighten(allow, HookVerdict("deny", "no", "guard"))
    assert denied.action == "deny" and denied.rule == "hook:guard" and "guard" in denied.reason

    asked = tighten(allow, HookVerdict("confirm", "late", "guard"))
    assert asked.action == "confirm" and asked.mode_outcome == "allow" and "late" in asked.reason

    unapproved = tighten(confirm, HookVerdict("confirm", "", "guard"))
    assert unapproved.preapproved is False and unapproved.mode_outcome == "confirm"


def test_a_hook_confirmation_does_not_widen_the_sandbox_mode(tmp_path):
    policy = OdessyPolicy(workspace_root=tmp_path)
    decision = policy.evaluate(PWD)
    assert decision.action == "allow"
    asked = tighten(decision, HookVerdict("confirm", "", "guard"))
    assert policy.requirement_for(PWD, asked).policy.mode == "read-only"


# --- the protocol --------------------------------------------------------------

@pytest.mark.anyio
async def test_timeout_stops_descendants_holding_output_pipes(tmp_path):
    marker = tmp_path / "survived"
    child = f'import time,pathlib;time.sleep(2);pathlib.Path({str(marker)!r}).write_text("escaped")'
    parent = f'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",{child!r}]);time.sleep(30)'
    started = time.monotonic()
    result = await run_hook(
        Hook("pre_tool", "tree", (sys.executable, "-c", parent), timeout=.2), {}, cwd=tmp_path,
    )
    assert time.monotonic() - started < 1.5
    assert "timed out" in result.error
    await asyncio.sleep(2.1)
    assert not marker.exists()

@pytest.mark.anyio
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_hook_output_is_bounded_while_reading(tmp_path, stream):
    program = f'import sys;sys.{stream}.write("x"*200000);sys.{stream}.flush()'
    result = await run_hook(
        Hook("pre_tool", "flood", (sys.executable, "-c", program), timeout=3), {}, cwd=tmp_path,
    )
    assert result.error and "output limit" in result.error
    assert len(result.stdout) <= 65536 and len(result.stderr) <= 65536


@pytest.mark.anyio
async def test_cancellation_stops_hook_descendants(tmp_path):
    marker = tmp_path / "child-started"
    survived = tmp_path / "child-survived"
    child = (f'import pathlib,time;pathlib.Path({str(marker)!r}).touch();'
             f'time.sleep(1);pathlib.Path({str(survived)!r}).touch()')
    parent = (f'import subprocess,sys,time;'
              f'subprocess.Popen([sys.executable,"-c",{child!r}]);time.sleep(30)')
    task = asyncio.create_task(run_hook(
        Hook("pre_tool", "cancel-tree", (sys.executable, "-c", parent), timeout=10),
        {}, cwd=tmp_path,
    ))
    try:
        for _ in range(200):
            if marker.exists():
                break
            await asyncio.sleep(.01)
        assert marker.exists(), "hook child did not start"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    await asyncio.sleep(1.1)
    assert not survived.exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "action", "reason"),
    [
        ("allow", "allow", ""),
        ("deny", "deny", "force push is not allowed here"),
        ("confirm", "confirm", "outside office hours"),
        ("garbage", "deny", "not a decision"),
    ],
)
async def test_pre_tool_answers(tmp_path, mode, action, reason):
    hooks = HookSet((hook(tmp_path, "pre_tool", mode),), tmp_path)
    verdict = await hooks.pre_tool(PWD, PolicyDecision("allow", "ok", "low"))
    assert verdict.action == action
    assert reason in verdict.reason
    (payload,) = records(tmp_path)
    assert payload["tool"] == "terminal.run" and payload["arguments"]["command"] == "pwd"
    assert payload["policy"] == {"decision": "allow", "risk": "low"}


@pytest.mark.anyio
async def test_a_slow_hook_times_out_and_on_error_decides(tmp_path):
    slow = hook(tmp_path, "pre_tool", "sleep", timeout=0.5, on_error="confirm")
    verdict = await HookSet((slow,), tmp_path).pre_tool(PWD, PolicyDecision("allow", "ok", "low"))
    assert verdict.action == "confirm" and "timed out" in verdict.reason


@pytest.mark.anyio
async def test_a_hook_that_cannot_start_denies(tmp_path):
    missing = Hook("pre_tool", "missing", ("no-such-program-odessy",))
    verdict = await HookSet((missing,), tmp_path).pre_tool(PWD, PolicyDecision("allow", "ok", "low"))
    assert verdict.action == "deny" and "cannot start" in verdict.reason


@pytest.mark.anyio
async def test_the_strictest_hook_wins_and_a_deny_stops_the_rest(tmp_path):
    hooks = HookSet(
        (
            hook(tmp_path, "pre_tool", "confirm"),
            hook(tmp_path, "pre_tool", "deny"),
            hook(tmp_path, "pre_tool", "allow"),
        ),
        tmp_path,
    )
    verdict = await hooks.pre_tool(PWD, PolicyDecision("allow", "ok", "low"))
    assert (verdict.action, verdict.hook) == ("deny", "deny")
    assert [r["hook"] for r in verdict.results] == ["confirm", "deny"]


@pytest.mark.anyio
async def test_a_hook_gets_no_api_keys(tmp_path):
    environ = {"PATH": "x", "SYSTEMROOT": "C:\\Windows", "ANTHROPIC_API_KEY": "sk-secret", "ODESSY_CLOUD_API_KEY": "k"}
    run = await run_hook(hook(tmp_path, "pre_tool", "allow"), {"event": "pre_tool"}, cwd=tmp_path, environ={**environ})
    assert run.code == 0
    env = set(records(tmp_path)[0]["env"])
    assert "ANTHROPIC_API_KEY" not in env and "ODESSY_CLOUD_API_KEY" not in env
    assert {"ODESSY_HOOK_EVENT", "ODESSY_WORKSPACE"} <= env


# --- in the agent --------------------------------------------------------------


@pytest.mark.anyio
async def test_a_pre_tool_deny_stops_the_call(tmp_path):
    model = Scripted(PWD, FinalResponse("done"))
    with pytest.raises(PermissionError, match="blocked by the deny hook"):
        await agent(tmp_path, model, [hook(tmp_path, "pre_tool", "deny", match=("terminal.*",))]).run_turn("pwd")
    assert model.observations == []


@pytest.mark.anyio
async def test_a_pre_tool_confirmation_asks_and_keeps_read_only(tmp_path):
    asked = []

    def confirm(decision, request):
        asked.append(decision)
        return True

    model = Scripted(PWD, FinalResponse("done"))
    result = await agent(tmp_path, model, [hook(tmp_path, "pre_tool", "confirm")], confirm=confirm).run_turn("pwd")
    assert len(asked) == 1 and "outside office hours" in asked[0].reason
    modes = [e.detail["mode"] for e in result.log if e.event == "execution_mode_resolved"]
    assert modes == ["read-only"]
    assert [e.detail["decision"] for e in result.log if e.event == "hook_decision"] == ["confirm"]


@pytest.mark.anyio
async def test_a_declined_hook_confirmation_does_not_run(tmp_path):
    model = Scripted(PWD, FinalResponse("done"))
    with pytest.raises(PermissionError, match="confirmation"):
        await agent(tmp_path, model, [hook(tmp_path, "pre_tool", "confirm")], confirm=False).run_turn("pwd")


@pytest.mark.anyio
async def test_hooks_that_do_not_match_do_not_run(tmp_path):
    model = Scripted(PWD, FinalResponse("done"))
    await agent(tmp_path, model, [hook(tmp_path, "pre_tool", "deny", match=("filesystem.*",))]).run_turn("pwd")
    assert records(tmp_path) == []


@pytest.mark.anyio
async def test_post_tool_notes_are_added_beside_the_result(tmp_path):
    model = Scripted(PWD, FinalResponse("done"))
    hooks = [hook(tmp_path, "post_tool", "note"), hook(tmp_path, "post_tool", "fail-note")]
    result = await agent(tmp_path, model, hooks).run_turn("pwd")
    notes = model.observations[0].result["hook_notes"]
    assert notes["source"] == "the user's own hooks, not the tool"
    assert [(n["hook"], n["exit_code"], n["note"]) for n in notes["notes"]] == [
        ("note", 0, "lint: 0 problems"),
        ("fail-note", 1, "lint: 2 problems"),
    ]
    assert model.observations[0].result["status"] == "ok"
    payload = records(tmp_path)[0]
    assert payload["event"] == "post_tool" and payload["result"]["status"] == "ok"
    assert [e.event for e in result.log].count("hook_note") == 2


@pytest.mark.anyio
async def test_turn_end_hooks_run_after_the_answer(tmp_path):
    model = Scripted(PWD, FinalResponse("done"))
    runner = agent(tmp_path, model, [hook(tmp_path, "turn_end", "allow")])
    result = await runner.run_turn("pwd please")
    assert result.final_text == "done"
    await runner.wait_for_hooks()
    (payload,) = records(tmp_path)
    assert payload["event"] == "turn_end" and payload["outcome"] == "answered"
    assert payload["final_text"] == "done" and payload["request"] == "pwd please"
    assert payload["tools"] == ["terminal.run"]


@pytest.mark.anyio
async def test_batch_calls_are_screened_once_each(tmp_path):
    batch = ToolBatch((ToolCall("a", ToolRequest("time.now")), ToolCall("b", ToolRequest("time.now"))))
    model = Scripted(batch, FinalResponse("done"))
    result = await agent(tmp_path, model, [hook(tmp_path, "pre_tool", "allow")]).run_turn("time")
    assert result.final_text == "done"
    assert [r["tool"] for r in records(tmp_path)] == ["time.now", "time.now"]
    assert [o.result.get("status", "ok") for o in model.observations] == ["ok", "ok"]


@pytest.mark.anyio
async def test_a_batch_call_denied_by_a_hook_stops_the_batch(tmp_path):
    batch = ToolBatch((ToolCall("a", ToolRequest("time.now")), ToolCall("b", PWD)))
    model = Scripted(batch, FinalResponse("done"))
    result = await agent(tmp_path, model, [hook(tmp_path, "pre_tool", "deny", match=("terminal.*",))]).run_turn("x")
    assert "blocked by the deny hook" in result.final_text
    assert not any(e.event == "mcp_tool_called" for e in result.log)


def test_an_unscreened_call_is_denied_when_a_hook_matches(tmp_path):
    runner = agent(tmp_path, Scripted(), [hook(tmp_path, "pre_tool", "allow", match=("terminal.*",))])
    assert runner._evaluate(PWD).action == "deny"
    assert runner._evaluate(ToolRequest("time.now")).action == "allow"


@pytest.mark.anyio
async def test_the_observer_sees_entries_as_they_happen(tmp_path):
    seen = []
    model = Scripted(PWD, FinalResponse("done"))
    await agent(tmp_path, model, None).run_turn("pwd", observer=seen.append)
    events = [entry.event for entry in seen]
    assert events.index("tool_dispatch") < events.index("mcp_tool_called") < events.index("tool_observation")
    public = [public_event(entry) for entry in seen if public_event(entry)]
    assert ("tool", {"phase": "started", "name": "terminal.run"}) in public
    assert ("tool", {"phase": "finished", "name": "terminal.run", "status": "ok"}) in public
    assert ("policy", {"name": "terminal.run", "outcome": "allowed", "risk": "low"}) in public


@pytest.mark.anyio
async def test_an_observer_that_raises_does_not_stop_the_turn(tmp_path):
    def broken(entry):
        raise RuntimeError("observer bug")

    model = Scripted(PWD, FinalResponse("done"))
    result = await agent(tmp_path, model, None).run_turn("pwd", observer=broken)
    assert result.final_text == "done"


def test_public_events_leave_out_arguments_results_and_reasons():
    entry = LogEntry("hook_decision", {"name": "terminal.run", "hook": "guard", "decision": "deny", "reason": "secret path"})
    assert public_event(entry) == ("hook", {"event": "pre_tool", "name": "terminal.run", "hook": "guard", "decision": "deny"})
    assert public_event(LogEntry("model_tool_request", {"arguments": {"x": 1}})) is None
    assert HookRun().error is None


def test_the_session_publishes_tool_events_without_arguments(tmp_path):
    import asyncio

    from odessy.application.session import SessionController

    async def run():
        controller = SessionController(
            model_factory=lambda name: object(),
            agent_factory=lambda model, **kwargs: agent(tmp_path, Scripted(PWD, FinalResponse("done")), None),
            profiles={"local": {}},
        )
        await controller.run_turn("pwd")
        await controller.close()
        return controller.events

    events = asyncio.run(run())
    tools = [event["data"] for event in events if event["type"] == "tool"]
    assert [t["phase"] for t in tools] == ["started", "finished"]
    policy = [event["data"] for event in events if event["type"] == "policy"]
    assert policy == [{"name": "terminal.run", "outcome": "allowed", "risk": "low"}]
    assert "pwd" not in json.dumps(tools + policy)


@pytest.mark.anyio
async def test_the_bound_approval_path_asks_and_names_the_hook(tmp_path):
    plans = []

    async def confirm_action(plan):
        plans.append(plan)
        return True

    runner = agent(tmp_path, Scripted(PWD, FinalResponse("done")), [hook(tmp_path, "pre_tool", "confirm", name="hours")])
    runner._confirm_action = confirm_action
    result = await runner.run_turn("pwd")
    assert result.final_text == "done"
    (plan,) = plans
    assert plan["scopes"] == ["authorization"]
    assert "your hours hook" in plan["authoritative_prompt"]
    assert [e.detail["mode"] for e in result.log if e.event == "execution_mode_resolved"] == ["read-only"]
    assert len(records(tmp_path)) == 1
