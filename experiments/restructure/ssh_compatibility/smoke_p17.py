"""Prompt 17 check: fixture-backed SSH transport, plus opt-in live probes.

The fixture check needs no model, no remote machine and no network. It substitutes
the ``ssh`` executable with a small stub that starts the real fixture MCP server, so
the production ``ServerConfig``/``mcp.Client`` stdio path is exercised end to end;
only SSH itself is replaced. That is a fixture result, not proof that SSH works.

The live paths are explicit and read-only by default:

    # local identity only: resolve an alias and read known_hosts; no connection
    python smoke_p17.py --alias jetson --inspect

    # one bounded login that runs `printf` only
    python smoke_p17.py --alias jetson --test

    # one bounded read-only status sample (load average + root disk)
    python smoke_p17.py --alias jetson --monitor

    # execution proof: list the remote tool surface (requires an installed Odessy)
    python smoke_p17.py --alias jetson --workspace /home/me/work \
        --remote-python /home/me/odessy/.venv/bin/python --execution

    # one controlled task, then a Stop attempt whose remote outcome must be checked
    python smoke_p17.py --alias jetson --workspace /home/me/work \
        --remote-python /home/me/odessy/.venv/bin/python --task --stop-test

Nothing here installs, edits ``sshd``/``authorized_keys``, accepts an unknown host
key, forwards the agent, or puts a secret in argv.
"""

from __future__ import annotations

import argparse
import asyncio
import builtins
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))

import ssh_compat  # noqa: E402

#: Python 3.10 has no ExceptionGroup; an empty tuple catches nothing there.
GROUP = getattr(builtins, "BaseExceptionGroup", ())

FIXTURE_SERVER = '''\
import asyncio

from mcp.server import MCPServer

server = MCPServer("p17-ssh-fixture", version="0.0.1")


@server.tool(name="echo")
def echo(text: str) -> dict:
    """Echo text back."""
    return {"echoed": text}


@server.tool(name="status.read")
def status_read() -> dict:
    """Return one fixed status sample."""
    return {"load_1m": 0.42, "disk_used_percent": 31}


@server.tool(name="slow")
async def slow(seconds: float = 30.0) -> dict:
    """Sleep, so a Stop attempt has something to interrupt."""
    await asyncio.sleep(seconds)
    return {"slept": seconds}


if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
'''

STUB_SSH = '''\
import os
import sys

args = sys.argv[1:]
if not args:
    sys.exit("stub ssh: no arguments")
remote = args[-1]
if "odessy.tools.stdio_server" not in remote or "--workspace" not in remote:
    sys.exit("stub ssh: unexpected remote command: " + remote)
os.execv(sys.executable, [sys.executable, {fixture!r}])
'''


def _fixture_config(work: Path, computer: ssh_compat.SshAlias, remote: str):
    from odessy.tools.external_tools import ServerConfig

    fixture = work / "fixture_server.py"
    fixture.write_text(FIXTURE_SERVER, encoding="utf-8")
    stub = work / "stub_ssh.py"
    stub.write_text(STUB_SSH.format(fixture=str(fixture)), encoding="utf-8")
    command = (sys.executable, str(stub), *ssh_compat.ssh_argv(computer, remote, ssh="ssh"))
    return ServerConfig(
        name="ssh-fixture",
        transport="stdio",
        command=command,
        env=(),
        timeout=30,
        target_metadata={"transport": "ssh", "ssh_alias": computer.alias},
    )


async def _exercise(config) -> dict[str, object]:
    from mcp import Client

    started = time.perf_counter()
    async with Client(config.connection()) as client:
        init_ms = (time.perf_counter() - started) * 1000
        info = getattr(client, "server_info", None)
        listed = await client.list_tools()
        tools = [tool.name for tool in getattr(listed, "tools", listed)]
        call_started = time.perf_counter()
        result = await client.call_tool("echo", {"text": "hello"})
        call_ms = (time.perf_counter() - call_started) * 1000
        text = _text_of(result)
        status = await client.call_tool("status.read", {})
        status_text = _text_of(status)

    stop_started = time.perf_counter()
    timed_out = False
    cleanup = "clean"
    try:
        async with Client(config.connection()) as client:
            await asyncio.wait_for(client.call_tool("slow", {"seconds": 30.0}), timeout=0.4)
    except asyncio.TimeoutError:
        timed_out = True
    except BaseException as error:  # a cancelled call surfaces while the session closes
        if not isinstance(error, GROUP):
            raise
        timed_out = "TimeoutError" in repr(error)
        cleanup = f"{error.__class__.__name__} while closing"
    stop_ms = (time.perf_counter() - stop_started) * 1000

    return {
        "mode": "fixture",
        "server": getattr(info, "name", None) if info is not None else None,
        "tools": tools,
        "echo": text,
        "status": status_text,
        "timeout_observed": timed_out,
        "stop_cleanup": cleanup,
        "remote_outcome": "unknown: only a real host can show the process tree",
        "timings_ms": {"initialize": round(init_ms, 1), "echo": round(call_ms, 1),
                       "stop_attempt": round(stop_ms, 1)},
    }


def _text_of(result) -> str:
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            return text
    return ""


def _describe(error: BaseException) -> str:
    """The leaf errors of an ExceptionGroup, so a failed remote import is visible."""

    leaves: list[str] = []
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        children = getattr(current, "exceptions", None) if isinstance(current, GROUP) else None
        if children:
            pending.extend(children)
        else:
            leaves.append(f"{current.__class__.__name__}: {current}")
    return "; ".join(leaves) or error.__class__.__name__


def _print(value: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    for key, item in value.items():
        print(f"{key}: {item}")


def _live(args, as_json: bool) -> int:
    computer = ssh_compat.describe(
        args.alias,
        ssh=ssh_compat.default_ssh(),
        ssh_keygen=ssh_compat.default_ssh_keygen(),
        config_path=args.config,
    )
    shown = computer.shown()
    _print({"identity": shown}, as_json)

    if not computer.identity.known:
        if as_json:
            print(json.dumps({"refused": "host key is not in known_hosts"}, indent=2))
        else:
            print("refused: the host key is not in known_hosts; add it manually, "
                  "never by accepting a prompt from this tool")
        return 3
    if args.inspect and not (args.test or args.monitor or args.execution):
        return 0

    abilities = {"transport": False, "monitoring": False, "execution": False,
                 "workspace": False}
    if args.test:
        probe = ssh_compat.probe_transport(computer.alias)
        _print({"transport": probe.shown()}, as_json)
        abilities["transport"] = probe.ok
        if not probe.ok:
            return 4

    if args.monitor:
        sample = ssh_compat.collect_status(computer)
        _print({"monitoring": sample.shown()}, as_json)
        if not isinstance(sample, ssh_compat.Unavailable):
            abilities["monitoring"] = True

    if args.execution or args.task or args.stop_test:
        if not args.workspace or not args.remote_python:
            print("--execution/--task/--stop-test need --workspace and --remote-python",
                  file=sys.stderr)
            return 2
        config = ssh_compat.server_config(
            computer, args.remote_python, args.workspace,
            mode=args.mode, isolation=args.isolation, network=args.network,
        )
        try:
            tools = asyncio.run(_list_tools(config))
        except BaseException as error:  # noqa: BLE001 - report the leaf failure
            _print({"execution": {"error": _describe(error)}}, as_json)
            return 5
        _print({"execution": {"tools": tools}}, as_json)
        abilities["workspace"] = True
        abilities["execution"] = bool(tools)
        if args.task:
            outcome = asyncio.run(_call(config, "terminal.run",
                                        {"command": "printf p17-ok", "timeout": 10}))
            _print({"task": outcome}, as_json)
        if args.stop_test:
            outcome = asyncio.run(_stop(config))
            _print({"stop": outcome}, as_json)

    _print({"capabilities": abilities}, as_json)
    return 0


async def _list_tools(config) -> list[str]:
    from mcp import Client

    # Raise the server's own failure instead of a TaskGroup wrapper, so a missing
    # remote module is reported as that module and not as an opaque ExceptionGroup.
    async with Client(config.connection(), raise_exceptions=True) as client:
        listed = await client.list_tools()
        return [tool.name for tool in getattr(listed, "tools", listed)]


async def _call(config, name: str, arguments: dict) -> dict[str, object]:
    from mcp import Client

    started = time.perf_counter()
    async with Client(config.connection()) as client:
        result = await client.call_tool(name, arguments)
    return {"tool": name, "result": _text_of(result),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}


async def _stop(config) -> dict[str, object]:
    from mcp import Client

    started = time.perf_counter()
    try:
        async with Client(config.connection()) as client:
            result = await asyncio.wait_for(
                client.call_tool("terminal.run", {"command": "sleep 30", "timeout": 30}),
                timeout=1.0,
            )
        return {"observed": "the call returned within 1 s; no Stop was needed",
                "result": _text_of(result)}
    except asyncio.TimeoutError:
        return {
            "observed": "the client stopped waiting and closed the session",
            "remote_outcome": "unknown",
            "check": "on the host run: pgrep -af 'sleep 30'",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except BaseException as error:
        if not isinstance(error, GROUP):
            raise
        return {
            "observed": "the client stopped waiting; closing the session raised",
            "cleanup": error.__class__.__name__,
            "remote_outcome": "unknown",
            "check": "on the host run: pgrep -af 'sleep 30'",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="smoke_p17")
    parser.add_argument("--self-test", action="store_true",
                        help="run the fixture-backed stdio round trip (no SSH, no network)")
    parser.add_argument("--alias")
    parser.add_argument("--config", help="an explicit ssh_config file (ssh -F)")
    parser.add_argument("--inspect", action="store_true",
                        help="resolve the alias and known_hosts only; no connection")
    parser.add_argument("--test", action="store_true", help="one bounded SSH login")
    parser.add_argument("--monitor", action="store_true", help="one bounded status sample")
    parser.add_argument("--execution", action="store_true", help="list the remote tools")
    parser.add_argument("--task", action="store_true", help="run one side-effect-free tool")
    parser.add_argument("--stop-test", action="store_true", help="attempt one Stop")
    parser.add_argument("--workspace")
    parser.add_argument("--remote-python")
    parser.add_argument("--mode", default="read-only",
                        choices=("read-only", "workspace-write"))
    parser.add_argument("--isolation", default="preferred",
                        choices=("preferred", "required", "not-required", "auto"))
    parser.add_argument("--network", default="deny", choices=("allow", "deny"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        with tempfile.TemporaryDirectory(prefix="p17-ssh-") as tmp:
            work = Path(tmp)
            computer = ssh_compat.SshAlias(
                alias="fixture-host", hostname="192.0.2.10", user="tester", port=22,
            )
            remote = ssh_compat.remote_stdio_command(
                "/opt/odessy/bin/python", "/home/tester/work")
            config = _fixture_config(work, computer, remote)
            try:
                result = asyncio.run(_exercise(config))
            except Exception as error:  # noqa: BLE001 - a failed check, not a traceback
                print(f"fixture check failed: {error.__class__.__name__}: {error}",
                      file=sys.stderr)
                return 1
            result["capabilities"] = ssh_compat.capabilities(
                transport=False, monitoring_enabled=False, monitoring_ok=False,
                execution_enabled=False, tools_listed=False, workspace_ok=False,
            ).as_dict()
            _print(result, args.json)
        return 0

    if not args.alias:
        parser.print_help()
        return 2
    return _live(args, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
