"""Policy, hooks and sandbox together: where one has to know what another did."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from odessy.authority.action_approval import build_action_approval
from odessy.authority.audit import ExternalAuditLog, audit_scope
from odessy.authority.policy import OdessyPolicy
from odessy.config.guard import ConfigGuard, reset_guard
from odessy.config.sandbox import SandboxSettings
from odessy.runtime import hooks as hooks_module
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.hook_rules import RuleConfigError, parse_rules
from odessy.runtime.hooks import (
    Hook,
    HookConfigError,
    HookSet,
    find_program,
    load_hooks,
    reset_baselines,
    run_hook,
)
from odessy.runtime.types import FinalResponse, ToolBatch, ToolCall, ToolRequest
from odessy.tools.mcp_server import build_odessy_mcp_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_state():
    reset_baselines()
    reset_guard()
    yield
    reset_baselines()
    reset_guard()


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "hooks"
    folder.mkdir()
    return folder


class Scripted:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.seen = []

    def propose(self, text):
        return self.actions.pop(0)

    def respond_to_observation(self, text, observation):
        self.seen.append(observation)
        return self.actions.pop(0)

    def respond_to_observations(self, text, observations):
        self.seen.extend(observations)
        return self.actions.pop(0)


def agent_for(workspace: Path, *actions, hooks=None, policy=None, **fields) -> OdessyAgent:
    return OdessyAgent(model=Scripted(*actions), policy=policy or OdessyPolicy(workspace_root=workspace),
                       mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace)),
                       confirm=fields.pop("confirm", True), hooks=hooks, **fields)


def mkdir(name: str) -> ToolRequest:
    return ToolRequest("terminal.run", {"command": f"mkdir {name}", "timeout": 10})


def batch(*requests: ToolRequest) -> ToolBatch:
    return ToolBatch(tuple(ToolCall(f"c{index}", request) for index, request in enumerate(requests)))


def rules(tmp_path: Path, *entries: dict) -> tuple:
    return tuple(rule for rule, _ in parse_rules(tmp_path / "hooks.toml", {"rule": list(entries)}))


def decide(workspace: Path, command: str, tool: str = "terminal.run", **arguments):
    return OdessyPolicy(workspace_root=workspace).evaluate(ToolRequest(tool, {"command": command, **arguments}))


# --- A: a batch is not a way round the hooks -----------------------------------------


@pytest.mark.anyio
async def test_a_taint_from_an_earlier_call_reaches_the_rest_of_the_batch(workspace, tmp_path):
    guard = rules(tmp_path,
                  {"name": "mark", "match": "terminal.run", "command_regex": "mkdir first", "decision": "taint", "taint": "seen"},
                  {"name": "stop-after", "match": "terminal.run", "when_taint": "seen", "decision": "deny"})
    agent = agent_for(workspace, batch(mkdir("first"), mkdir("second")), FinalResponse("done"), hooks=HookSet((), workspace, rules=guard))
    await agent.run_turn("go")
    assert (workspace / "first").is_dir() and not (workspace / "second").exists()


@pytest.mark.anyio
async def test_a_post_tool_stop_ends_the_batch_where_it_is(workspace, outside):
    script = outside / "stop.py"
    script.write_text('import json\nprint(json.dumps({"decision": "stop", "reason": "enough"}))\n', encoding="utf-8")
    hook = Hook(event="post_tool", name="stopper", command=(sys.executable, str(script)), timeout=20)
    agent = agent_for(workspace, batch(mkdir("one"), mkdir("two")), FinalResponse("done"), hooks=HookSet((hook,), workspace))
    result = await agent.run_turn("go")
    assert (workspace / "one").is_dir() and not (workspace / "two").exists()
    assert "Stopped by the stopper hook" in result.final_text


@pytest.mark.anyio
async def test_a_script_edited_by_an_earlier_call_stops_the_batch(workspace, outside):
    script = outside / "guard.py"
    script.write_text("pass\n", encoding="utf-8")
    path = outside / "hooks.toml"
    path.write_text(f'[[post_tool]]\nname = "guard"\ncommand = ["{Path(sys.executable).as_posix()}", "{script.as_posix()}"]\n', encoding="utf-8")

    def edit(decision, request):
        script.write_text("pass  # edited\n", encoding="utf-8")
        return True

    agent = agent_for(workspace, batch(mkdir("one"), mkdir("two")), FinalResponse("done"), hooks=load_hooks(workspace, path), confirm=edit)
    result = await agent.run_turn("go")
    assert not (workspace / "two").exists() and "integrity" in result.final_text


@pytest.mark.anyio
async def test_a_batch_without_a_stop_still_runs_every_call(workspace):
    await agent_for(workspace, batch(mkdir("one"), mkdir("two")), FinalResponse("done"), hooks=HookSet((), workspace)).run_turn("go")
    assert (workspace / "one").is_dir() and (workspace / "two").is_dir()


# --- B: a hook never runs the workspace's code outside the sandbox --------------------


def program_in(folder: Path, name: str) -> Path:
    path = folder / (name + (".exe" if os.name == "nt" else ""))
    shutil.copy(sys.executable if os.name != "nt" else Path(os.environ["SYSTEMROOT"]) / "System32" / "where.exe", path)
    path.chmod(0o755)
    return path


def test_a_bare_name_is_never_found_in_the_workspace(workspace, outside):
    program_in(workspace, "probetool")
    env = {"PATH": str(outside), "PATHEXT": ".EXE"}
    assert find_program("probetool", workspace, env) is None
    found = program_in(outside, "probetool")
    assert Path(find_program("probetool", workspace, env)) == found


def test_a_workspace_folder_on_path_is_skipped(workspace, outside):
    venv = workspace / ".venv"
    venv.mkdir()
    program_in(venv, "probetool")
    env = {"PATH": os.pathsep.join((str(venv), ".", "", str(outside))), "PATHEXT": ".EXE"}
    assert find_program("probetool", workspace, env) is None


def test_a_path_into_the_workspace_is_refused(workspace, outside):
    assert find_program("./tool", workspace, {}) is None
    assert find_program(str(workspace / "tool"), workspace, {}) is None
    assert find_program(str(outside / "tool"), workspace, {}) == str(outside / "tool")


@pytest.mark.anyio
async def test_a_workspace_program_does_not_run_as_a_hook(workspace):
    program_in(workspace, "probetool")
    run = await run_hook(Hook(event="post_tool", name="lint", command=("probetool",), timeout=5), {}, cwd=workspace)
    assert "not found outside the workspace" in run.error


@pytest.mark.parametrize(
    "command",
    [["python", "-m", "pytest"], ["python3.12", "-c", "pass"], ["npx", "eslint", "."], ["npm", "test"], ["pytest"],
     ["make", "check"], ["go", "test", "./..."]],
)
def test_a_hook_that_runs_workspace_code_needs_the_sandbox(workspace, outside, command):
    path = outside / "hooks.toml"
    text = f'[[post_tool]]\nname = "lint"\ncommand = {json.dumps(command)}\n'
    path.write_text(text, encoding="utf-8")
    with pytest.raises(HookConfigError, match='sandbox = "read-only"'):
        load_hooks(workspace, path)
    path.write_text(text + 'sandbox = "read-only"\n', encoding="utf-8")
    assert load_hooks(workspace, path).hooks[0].sandbox == "read-only"


def test_a_tool_that_only_reads_is_left_alone(workspace, outside):
    path = outside / "hooks.toml"
    path.write_text('[[post_tool]]\nname = "lint"\ncommand = ["ruff", "check", "."]\n', encoding="utf-8")
    assert load_hooks(workspace, path).hooks[0].sandbox is None


def test_an_isolated_python_is_not_workspace_code(workspace, outside):
    path = outside / "hooks.toml"
    path.write_text('[[post_tool]]\nname = "lint"\ncommand = ["python", "-I", "-m", "json.tool"]\n', encoding="utf-8")
    assert load_hooks(workspace, path) is not None


def test_a_script_in_an_extra_writable_folder_is_refused(workspace, outside, monkeypatch):
    script = outside / "guard.py"
    script.write_text("pass\n", encoding="utf-8")
    path = outside / "hooks.toml"
    path.write_text(f'[[pre_tool]]\nname = "g"\ncommand = ["python", "{script.as_posix()}"]\n', encoding="utf-8")
    settings = SandboxSettings(extra_writable=(str(outside),))
    monkeypatch.setattr("odessy.config.sandbox.load_sandbox_settings", lambda environ=None: settings)
    with pytest.raises(HookConfigError, match="extra_writable"):
        load_hooks(workspace, path)


def test_the_program_a_name_resolves_to_is_watched(workspace, outside, monkeypatch):
    program = program_in(outside, "probetool")
    monkeypatch.setenv("PATH", str(outside))
    path = outside / "hooks.toml"
    path.write_text('[[pre_tool]]\nname = "g"\ncommand = ["probetool"]\n', encoding="utf-8")
    hooks = load_hooks(workspace, path, track=False)
    assert hooks.verify().tampered is None
    program.write_bytes(b"replaced")
    assert hooks.verify().tampered is not None


class Evaluation:
    def __init__(self, enforcement):
        self.enforcement = enforcement


class Provider:
    def confine(self, argv, policy):
        Provider.policy = policy
        return type("Result", (), {"argv": ("wrapped", *argv)})


class Selection:
    def __init__(self, enforcement):
        self.evaluation = Evaluation(enforcement)
        self.provider = Provider()
        self.requirement = type("Requirement", (), {"policy": None})


def registry_with(enforcement):
    class Registry:
        def select(self, requirement):
            selection = Selection(enforcement)
            selection.requirement.policy = requirement.policy
            return selection

    return lambda: Registry()


def test_a_sandboxed_hook_runs_read_only_with_no_network(workspace, monkeypatch):
    monkeypatch.setattr(hooks_module, "_sandbox_registry", registry_with("full"))
    assert hooks_module.confine_hook(("tool",), workspace, {})[0] == "wrapped"
    assert (Provider.policy.mode, Provider.policy.network) == ("read-only", "deny")


@pytest.mark.anyio
async def test_a_sandboxed_hook_does_not_run_without_a_sandbox(workspace, monkeypatch):
    monkeypatch.setattr(hooks_module, "_sandbox_registry", registry_with("none"))
    hook = Hook(event="post_tool", name="lint", command=(sys.executable, "-c", "pass"), timeout=5, sandbox="read-only")
    run = await run_hook(hook, {}, cwd=workspace)
    assert "no sandbox" in run.error


def test_hooks_do_not_import_from_the_working_directory():
    env = hooks_module.hook_environment({}, event="post_tool", workspace=Path("."))
    assert env["PYTHONSAFEPATH"] == "1"


# --- C: one answer for where a call runs ---------------------------------------------


def confined(workspace, **fields) -> OdessyPolicy:
    posture = {"local": {"read-only": ("bwrap", "full"), "workspace-write": ("bwrap", "full")}}
    return OdessyPolicy(workspace_root=workspace, posture=posture, **fields)


def plan_for(policy, request):
    return policy.execution_plan(request, policy.evaluate(request))


def test_the_plan_says_where_a_command_runs(workspace):
    policy = confined(workspace)
    assert plan_for(policy, ToolRequest("terminal.run", {"command": "ls"})) == {
        "target": "local", "mode": "read-only", "isolation": "preferred", "network": "deny",
        "provider": "bwrap", "enforcement": "full",
    }
    assert plan_for(policy, ToolRequest("terminal.run", {"command": "mkdir a"}))["network"] == "allow"
    assert plan_for(policy, ToolRequest("time.now", {})) is None


def test_the_plan_for_an_escalation_is_the_wider_mode(workspace):
    request = ToolRequest("terminal.run", {"command": "mkdir a", "sandbox_permissions": "danger-full-access", "justification": "x"})
    plan = plan_for(confined(workspace), request)
    assert (plan["mode"], plan["provider"], plan["enforcement"]) == ("danger-full-access", "unconfined", "none")


def test_the_laptop_is_not_forecast(workspace):
    plan = plan_for(confined(workspace), ToolRequest("laptop.terminal.run", {"command": "mkdir a"}))
    assert (plan["target"], plan["provider"], plan["enforcement"]) == ("laptop", None, None)


def prompt_for(policy, request):
    return build_action_approval(request, policy.evaluate(request), policy, call_id="c", execution_target="local")


def test_the_prompt_says_what_the_sandbox_will_do(workspace):
    plan = prompt_for(confined(workspace), ToolRequest("terminal.run", {"command": "mkdir a"}))
    assert "Runs workspace-write, network on, bwrap sandbox, full enforcement." in plan.authoritative_prompt
    assert plan.public_dict()["execution"]["provider"] == "bwrap"


def test_the_prompt_says_when_there_is_no_sandbox(workspace):
    posture = {"local": {"workspace-write": ("unconfined", "none")}}
    policy = OdessyPolicy(workspace_root=workspace, posture=posture)
    assert "NO SANDBOX" in prompt_for(policy, ToolRequest("terminal.run", {"command": "mkdir a"})).authoritative_prompt


def test_an_answer_does_not_survive_a_change_to_where_it_runs(workspace):
    request = ToolRequest("terminal.run", {"command": "mkdir a"})
    plan = prompt_for(confined(workspace), request)
    worse = OdessyPolicy(workspace_root=workspace, posture={"local": {"workspace-write": ("unconfined", "none")}})
    assert not plan.matches(request, worse.evaluate(request), worse, execution_target="local")


@pytest.mark.parametrize("command", ["git status", "git log --oneline"])
def test_a_git_read_is_asked_about_where_nothing_enforces_read_only(workspace, command):
    policy = OdessyPolicy(workspace_root=workspace, posture={"local": {"read-only": ("unconfined", "none")}})
    decision = policy.evaluate(ToolRequest("terminal.run", {"command": command}))
    assert (decision.action, decision.rule, decision.mode_outcome) == ("confirm", "command_unconfined_read", "allow")


def test_a_git_read_runs_unasked_where_read_only_is_enforced(workspace):
    assert confined(workspace).evaluate(ToolRequest("terminal.run", {"command": "git status"})).action == "allow"


def test_a_partial_sandbox_and_an_unprobed_host_keep_git_reads_unasked(workspace):
    partial = OdessyPolicy(workspace_root=workspace, posture={"local": {"read-only": ("windows-acl", "partial")}})
    assert partial.evaluate(ToolRequest("terminal.run", {"command": "git status"})).action == "allow"
    assert decide(workspace, "git status", tool="laptop.terminal.run").action == "allow"


def test_listing_runs_unasked_even_without_a_sandbox(workspace):
    policy = OdessyPolicy(workspace_root=workspace, posture={"local": {"read-only": ("unconfined", "none")}})
    assert policy.evaluate(ToolRequest("terminal.run", {"command": "ls"})).action == "allow"


@pytest.mark.parametrize(
    "command, network",
    [("npm install", "allow"), ("cd sub && npm install", "allow"), ("sudo pip install a", "allow"),
     ("pip install a && python -c 1", "deny"), ("npm install; curl https://x.example", "deny"), ("make", "deny")],
)
def test_only_a_pure_install_gets_back_a_cut_network(workspace, command, network):
    policy = OdessyPolicy(workspace_root=workspace, sandbox=SandboxSettings(network_workspace_write="deny"))
    assert policy.execution_arguments(ToolRequest("terminal.run", {"command": command}), "workspace-write")["network"] == network


# --- D: one credential list ----------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["cat ~/.config/gh/hosts.yml", "cat ~/.password-store/a.gpg", "cat ~/.local/share/keyrings/login.keyring",
     "ls ~/.config/gh", "ls ~/.mozilla", "ls ~/.config/odessy", "python x.py ~/.config/gcloud/credentials.db",
     "cat /home/me/.config/gh/hosts.yml", 'Get-Content "C:\\Users\\me\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cookies"'],
)
def test_every_hidden_path_is_also_refused(workspace, command):
    decision = decide(workspace, command)
    assert (decision.action, decision.rule) == ("deny", "command_credentials"), command


def test_the_sandbox_and_the_policy_share_one_list():
    from odessy.authority.secret_paths import CREDENTIAL_PATHS, names_credential_path
    from odessy.config.sandbox import DEFAULT_HIDDEN_PATHS

    assert DEFAULT_HIDDEN_PATHS is CREDENTIAL_PATHS
    assert all(names_credential_path(path) for path in CREDENTIAL_PATHS)


@pytest.mark.parametrize("word", ["config/gh.txt", ".config/ghost", "src/.config/ghost/a"])
def test_a_near_miss_is_not_a_credential(word):
    from odessy.authority.secret_paths import names_credential_path

    assert not names_credential_path(word)


# --- E: rules reach the sandbox ------------------------------------------------------


@pytest.mark.anyio
async def test_a_restrict_rule_cuts_the_network_without_a_question(workspace, tmp_path):
    guard = rules(tmp_path, {"name": "cut", "match": "terminal.run", "decision": "restrict", "network": "deny"})
    agent = agent_for(workspace, mkdir("first"), FinalResponse("done"), hooks=HookSet((), workspace, rules=guard), confirm=True)
    seen = {}
    original = agent._call_tool

    async def spy(name, arguments, registry):
        seen.update(arguments)
        return await original(name, arguments, registry)

    agent._call_tool = spy
    await agent.run_turn("go")
    assert seen["network"] == "deny" and (workspace / "first").is_dir()


@pytest.mark.anyio
async def test_a_taint_can_cut_the_network_for_the_rest_of_the_turn(workspace, tmp_path):
    guard = rules(tmp_path,
                  {"name": "mark", "match": "terminal.run", "command_regex": "mkdir first", "decision": "taint", "taint": "secret"},
                  {"name": "cut", "match": "terminal.run", "when_taint": "secret", "decision": "restrict", "network": "deny"})
    agent = agent_for(workspace, batch(mkdir("first"), mkdir("second")), FinalResponse("done"), hooks=HookSet((), workspace, rules=guard))
    sent = []
    original = agent._call_tool

    async def spy(name, arguments, registry):
        sent.append(arguments["network"])
        return await original(name, arguments, registry)

    agent._call_tool = spy
    await agent.run_turn("go")
    assert sent == ["allow", "deny"]


@pytest.mark.anyio
async def test_a_rule_can_match_the_policy_rule(workspace, tmp_path):
    guard = rules(tmp_path, {"name": "no-uploads", "match": "terminal.run", "policy_rule": "command_exfiltration", "decision": "deny"})
    policy = OdessyPolicy(workspace_root=workspace)
    hooks = HookSet((), workspace, rules=guard)
    upload = ToolRequest("terminal.run", {"command": "scp a me@host:/tmp"})
    assert (await hooks.pre_tool(upload, policy.evaluate(upload))).action == "deny"
    plain = ToolRequest("terminal.run", {"command": "mkdir a"})
    assert (await hooks.pre_tool(plain, policy.evaluate(plain))).action == "allow"


@pytest.mark.parametrize(
    "entry, message",
    [({"decision": "restrict"}, "needs network"), ({"decision": "deny", "network": "allow"}, "can only be"),
     ({"decision": "taint", "taint": "x", "network": "deny"}, "decides nothing")],
)
def test_network_rules_are_validated(tmp_path, entry, message):
    with pytest.raises(RuleConfigError, match=message):
        rules(tmp_path, {"name": "x", **entry})


def test_a_review_of_delegated_changes_ignores_a_restrict_rule(workspace, tmp_path):
    guard = rules(tmp_path, {"name": "cut", "match": "filesystem.write", "decision": "restrict", "network": "deny"})
    assert HookSet((), workspace, rules=guard).review_changes(["a.txt"]) == []


@pytest.mark.anyio
async def test_a_hook_program_sees_the_policy_rule_and_the_network(workspace, outside):
    record = outside / "seen.json"
    script = outside / "see.py"
    script.write_text(f"import sys\nopen({str(record)!r}, 'w').write(sys.stdin.read())\n", encoding="utf-8")
    hook = Hook(event="pre_tool", name="see", command=(sys.executable, str(script)), timeout=20)
    await agent_for(workspace, mkdir("a"), FinalResponse("done"), hooks=HookSet((hook,), workspace)).run_turn("go")
    payload = json.loads(record.read_text(encoding="utf-8"))
    assert payload["policy"]["rule"] == "command_write" and payload["execution"]["network"] == "allow"


def test_a_changed_config_file_is_noticed(tmp_path):
    path = tmp_path / "commands.toml"
    guard = ConfigGuard((path,))
    assert guard.changed() is None
    path.write_text("x", encoding="utf-8")
    assert "commands.toml changed" in guard.changed()
    assert "commands.toml changed" in ConfigGuard((path,)).changed()  # a new session keeps the first baseline


@pytest.mark.anyio
async def test_a_changed_config_file_refuses_every_call(workspace, tmp_path):
    path = tmp_path / "sandbox.toml"
    guard = ConfigGuard((path,))
    path.write_text("x", encoding="utf-8")
    log = tmp_path / "audit.jsonl"
    with audit_scope(ExternalAuditLog(log)), pytest.raises(PermissionError, match="restart Odessy"):
        await agent_for(workspace, mkdir("a"), config_guard=guard).run_turn("go")
    assert not (workspace / "a").exists()
    events = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert events.count("integrity") == 1


# --- F: the laptop's paths are the laptop's -------------------------------------------


@pytest.mark.parametrize("cwd", ["sub", "D:/proj", "sub/deep", "/home/me/proj", "."])
def test_a_laptop_folder_is_not_judged_here(workspace, cwd):
    assert decide(workspace, "npm test", tool="laptop.terminal.run", cwd=cwd).rule == "command_default"


@pytest.mark.parametrize("cwd", ["..", "../..", "sub/../..", "..\\.."])
def test_a_relative_laptop_folder_cannot_climb_out(workspace, cwd):
    assert decide(workspace, "npm test", tool="laptop.terminal.run", cwd=cwd).rule == "outside_workspace"


def test_a_reader_on_the_laptop_is_asked_about(workspace):
    assert decide(workspace, "cat notes.txt", tool="laptop.terminal.run").action == "confirm"
    assert decide(workspace, "cat notes.txt").action == "allow"
    assert decide(workspace, "ls", tool="laptop.terminal.run").action == "allow"


def test_a_local_folder_still_has_to_be_here(workspace):
    assert decide(workspace, "npm test", cwd="sub").rule == "outside_workspace"


# --- G: the rest ---------------------------------------------------------------------


@pytest.mark.parametrize("command", ["cat commands.toml", "cat docs/hooks.toml", "grep port sandbox.toml"])
def test_reading_a_file_with_a_protected_name_is_allowed(workspace, tmp_path, command):
    policy = OdessyPolicy(workspace_root=workspace, protected_paths=(tmp_path / "cfg" / "hooks.toml", tmp_path / "cfg" / "commands.toml",
                                                                     tmp_path / "cfg" / "sandbox.toml"))
    assert policy.evaluate(ToolRequest("terminal.run", {"command": command})).rule != "protected_config"


@pytest.mark.parametrize("command", ["echo x > commands.toml", "rm hooks.toml", "sed -i s/a/b/ sandbox.toml", "cp a.toml hooks.toml"])
def test_changing_a_file_with_a_protected_name_is_refused(workspace, tmp_path, command):
    policy = OdessyPolicy(workspace_root=workspace, protected_paths=(tmp_path / "cfg" / "hooks.toml", tmp_path / "cfg" / "commands.toml",
                                                                     tmp_path / "cfg" / "sandbox.toml"))
    assert policy.evaluate(ToolRequest("terminal.run", {"command": command})).rule == "protected_config"


def test_a_protected_path_is_refused_even_for_a_read(workspace, tmp_path):
    secret = tmp_path / "cfg" / "hooks.toml"
    policy = OdessyPolicy(workspace_root=workspace, protected_paths=(secret,))
    assert policy.evaluate(ToolRequest("terminal.run", {"command": f"cat {secret.as_posix()}"})).rule == "protected_config"


def denied_result() -> dict:
    return {"status": "failed", "sandbox": {"provider_id": "bwrap", "enforcement": "full", "denied": True}}


def test_a_sandbox_denial_tells_the_model_it_may_ask(workspace):
    agent = agent_for(workspace)
    result = denied_result()
    agent._hint_escalation(mkdir("a"), result)
    assert "escalation available" in result["sandbox_hint"]


def test_no_hint_where_escalation_is_off(workspace):
    settings = SandboxSettings(escalation="never")
    agent = agent_for(workspace, policy=OdessyPolicy(workspace_root=workspace, sandbox=settings))
    result = denied_result()
    agent._hint_escalation(mkdir("a"), result)
    assert "sandbox_hint" not in result


def test_no_hint_for_an_ordinary_failure(workspace):
    agent = agent_for(workspace)
    result = {"status": "failed", "sandbox": {"provider_id": "bwrap", "enforcement": "full"}}
    agent._hint_escalation(mkdir("a"), result)
    assert "sandbox_hint" not in result


def test_the_bridge_marks_a_denial_by_the_backend_s_own_signature(workspace, monkeypatch):
    from odessy.authority.command_bridge import CommandBridge

    failing = [sys.executable, "-c", "import sys; sys.stderr.write('read-only file system'); sys.exit(3)"]
    monkeypatch.setattr("odessy.authority.command_bridge._shell_command", lambda command: failing)
    monkeypatch.setattr(CommandBridge, "_confine", lambda self, argv, mode, isolation, shape: (
        argv, {"provider_id": "x", "enforcement": "full", "limitations": [], "denial_signatures": ("read-only file system",)}))
    result = CommandBridge(workspace).run(command="touch a")
    assert result["sandbox"]["denied"] is True and "denial_signatures" not in result["sandbox"]


@pytest.mark.anyio
async def test_the_audit_log_says_where_a_command_ran(workspace, tmp_path):
    log = tmp_path / "audit.jsonl"
    with audit_scope(ExternalAuditLog(log)):
        await agent_for(workspace, mkdir("a"), FinalResponse("done"), policy=confined(workspace)).run_turn("go")
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    decision = next(r for r in records if r["event"] == "decision")
    assert decision["execution"]["mode"] == "workspace-write" and decision["execution"]["provider"] == "bwrap"
    assert "sandbox" in next(r for r in records if r["event"] == "call")


@pytest.mark.anyio
async def test_a_job_is_screened_against_its_own_workspace(workspace, tmp_path, monkeypatch):
    from odessy.application.session import SessionController

    seen = []

    def fake(where):
        seen.append(Path(where))
        return None

    monkeypatch.setattr("odessy.runtime.hooks.load_hooks", fake)
    await SessionController._screen_direct(None, "agent.delegate", {"workspace": str(tmp_path)}, "high")
    assert seen == [tmp_path]


def test_the_doctor_says_what_the_sandbox_does_not_reach(tmp_path, monkeypatch):
    from odessy.config import doctor

    (check,) = doctor._sandbox_checks({"ODESSY_SANDBOX_FILE": str(tmp_path / "none.toml")})
    assert "run outside this sandbox" in check.summary

