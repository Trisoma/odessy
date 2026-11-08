"""Assertion-driven acceptance smoke for the pinned Harness runtime (ACP transport).

Every scenario computes a PASS/FAIL and the process exits **non-zero** if any check
fails. This is the gate; `smoke_sdk.py` remains a separate comparison probe.

Scenarios:

  C0  boot + initialize (protocol version, capabilities)
  C1  live text deltas cross the real plugin bridge *before* settlement
  C2  two turns run concurrently; cancelling one does not disturb the other
  C3  close + resume replays prior history
  C4  a real approval request is raised, answered, honoured, and nothing is written
  C5  an external MCP tool is attached and invoked
  C6  with the built-in tool rows disabled, the MCP tool is the only model tool

Prerequisites: the spike plugin bundle installed into the isolated profile
(`python install_plugin.py`), the pinned wheels, and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acp_client  # noqa: E402
import mock_provider  # noqa: E402
from stream_bridge import BridgeClient, free_port  # noqa: E402

EXPECTED_TEXT = "Hello from the deterministic fixture."

#: How long the gate keeps listening after a cancelled turn settles. Any text for
#: that turn observed in this window fails the run.
LATE_WINDOW_S = 1.5

#: Rows that expose a way to act on the machine. Disabled to prove the session has
#: exactly one execution path (Odessy's attached MCP server).
DISABLE_ROWS = [
    "tool-pwsh", "tool-bash", "tool-fs", "tool-fs-search", "tool-skill", "tool-web",
    "tool-subagent", "tool-subagent-control", "tool-subagent-list-agents",
    "tool-subagent-fork", "tool-workflow", "tool-todo", "tool-goal", "tool-jobs",
    "tool-working-directory", "plan-mode", "mcp-resources",
]

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))


def row_overlay(dump: str, row_id: str) -> str:
    """Re-emit one dumped row with `disabled: true`, preserving its config verbatim."""
    lines = dump.splitlines()
    start = next((i for i, line in enumerate(lines) if line.rstrip() == f"- id: {row_id}"), None)
    if start is None:
        raise SystemExit(f"row not found in dumped config: {row_id}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("- id: ") or lines[index].startswith("# =="):
            end = index
            break
    block = lines[start:end]
    while block and not block[-1].strip():
        block.pop()
    out: list[str] = []
    replaced = False
    for line in block:
        if line.startswith("  disabled:"):
            out.append("  disabled: true")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        index = next(i for i, line in enumerate(out) if line.startswith("  name:"))
        out.insert(index + 1, "  disabled: true")
    return "\n".join(out) + "\n"


def last_tools(requests_log: Path) -> list[str]:
    lines = [line for line in requests_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return list(json.loads(lines[-1]).get("tools") or []) if lines else []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    ws = work / "ws"
    for path in (work, ws, work / "mock", work / "mock-exclusive", work / "mcp"):
        path.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")
    (work / "mock-exclusive" / "requests.jsonl").write_text("", encoding="utf-8")
    marker = work / "outside-marker.txt"
    marker.unlink(missing_ok=True)

    # --- overlays -------------------------------------------------------
    acp_patch = work / "acp-patch.yml"
    acp_patch.write_text(
        "# Spike overlay: pin the provider route the ACP sessions use.\n"
        "- id: acp\n"
        "  config:\n"
        "    provider: deepseek-official\n"
        "    model: mock-model\n",
        encoding="utf-8",
    )
    bridge_port = free_port()
    bridge_token = secrets.token_hex(16)
    dump_env = acp_client.base_env(home, "http://127.0.0.1:1", bridge=(bridge_port, bridge_token))
    dump = subprocess.run([args.exe, "--profile", "acp", "--dump-config"], env=dump_env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    if dump.returncode != 0:
        print(dump.stderr[-2000:])
        return 2
    tools_off = work / "tools-off.yml"
    tools_off.write_text(
        "# Spike overlay: leave exactly one execution path (the attached MCP server).\n"
        + "".join(row_overlay(dump.stdout, row_id) for row_id in DISABLE_ROWS),
        encoding="utf-8",
    )

    # --- provider fixtures ---------------------------------------------
    server, port = mock_provider.serve(work / "mock")
    mock_provider.ESCALATION_TARGET = str(marker)
    base_url = f"http://127.0.0.1:{port}"
    server_ex, port_ex = mock_provider.serve(work / "mock-exclusive")
    base_url_ex = f"http://127.0.0.1:{port_ex}"

    permission_requests: list[dict] = []

    def on_request(client, method, params, message):
        if method == "session/request_permission":
            permission_requests.append(params)
            options = params.get("options") or []
            chosen = next((o for o in options if str(o.get("kind", "")).startswith("reject")), None)
            if chosen is None:
                return {"outcome": {"outcome": "cancelled"}}
            return {"outcome": {"outcome": "selected", "optionId": chosen.get("optionId")}}
        permission_requests.append({"method": method, "unsupported": True})
        return None

    env = acp_client.base_env(home, base_url, bridge=(bridge_port, bridge_token))
    started = time.perf_counter()
    client = acp_client.AcpClient(args.exe, env=env, cwd=str(ws), on_request=on_request,
                                  extra_args=["--patch", str(acp_patch)],
                                  stderr_path=work / "acp-stderr.log")
    bridge = BridgeClient("127.0.0.1", bridge_port, bridge_token)
    try:
        # --- C0 boot ----------------------------------------------------
        init = client.initialize()
        ready_s = round(time.perf_counter() - started, 3)
        check("C0 initialize protocolVersion == 1", init.get("protocolVersion") == 1, str(init.get("protocolVersion")))
        check("C0 session capabilities close/list/resume",
              set((init.get("agentCapabilities") or {}).get("sessionCapabilities") or {}) == {"close", "list", "resume"},
              str((init.get("agentCapabilities") or {}).get("sessionCapabilities")))
        check("C0 bridge authenticated", bridge.hello.get("type") == "hello", str(bridge.hello))

        # --- C1 live deltas --------------------------------------------
        created = client.new_session()
        session_a = created["sessionId"]
        turn = client.prompt(session_a, "hello")
        deltas = bridge.text_deltas(session_a)
        joined = "".join((frame.get("chunk") or {}).get("text", "") for frame in deltas)
        first_dt = (deltas[0]["_received"] - turn.started) if deltas else None
        check("C1 turn completed", turn.stop_reason == "end_turn", f"{turn.stop_reason} err={turn.error}")
        check("C1 >=3 bridged text deltas before settlement", len(deltas) >= 3, f"count={len(deltas)}")
        check("C1 first delta strictly before settlement",
              first_dt is not None and first_dt < turn.elapsed - 0.25,
              f"first={None if first_dt is None else round(first_dt, 3)}s settle={round(turn.elapsed, 3)}s")
        check("C1 bridged text equals final answer", joined == EXPECTED_TEXT, repr(joined))
        check("C1 ACP itself still settles text", any(
            (u.get("update") or {}).get("sessionUpdate") == "agent_message_chunk" for u in turn.updates))

        # --- C2 two turns genuinely running, cancel one -------------------
        # Both fixtures stream for seconds, so both turns are provably in flight when
        # the cancel arrives; the survivor must keep streaming *after* the cancel.
        session_b = client.new_session()["sessionId"]   # to be cancelled (60 x 250 ms)
        session_c = client.new_session()["sessionId"]   # survivor (20 x 250 ms)
        turn_b = client.prompt_async(session_b, "slow cancel please")
        turn_c = client.prompt_async(session_c, "slow survive please")
        # Long enough that the second session's provider stream is observable, short
        # enough that both turns are certainly still in flight.
        time.sleep(1.6)
        b_done_at_cancel = turn_b.done
        c_done_at_cancel = turn_c.done
        b_deltas_at_cancel = len(bridge.text_deltas(session_b))
        running_at_cancel = (not b_done_at_cancel) and (not c_done_at_cancel)
        cancel_at = time.perf_counter()
        client.cancel(session_b)
        turn_b.wait(120)
        b_deltas_at_settle = len(bridge.text_deltas(session_b))
        b_settle_at = turn_b.started + turn_b.elapsed
        # Bounded observation after the cancelled turn settled: no further text for it,
        # while the survivor must keep streaming throughout the same window.
        time.sleep(LATE_WINDOW_S)
        b_deltas_after_window = len(bridge.text_deltas(session_b))
        survivor_deltas_after_cancel = [
            f for f in bridge.text_deltas(session_c) if f["_received"] > cancel_at
        ]
        survivor_still_running = not turn_c.done
        turn_c.wait(120)
        settled_c = "".join(
            ((u.get("update") or {}).get("content") or {}).get("text", "")
            for u in turn_c.updates
            if (u.get("update") or {}).get("sessionUpdate") == "agent_message_chunk"
        )
        bridged_c = "".join((f.get("chunk") or {}).get("text", "") for f in bridge.text_deltas(session_c))
        check("C2 both turns still running at cancel time", running_at_cancel,
              f"cancelled_done={b_done_at_cancel} survivor_done={c_done_at_cancel}")
        check("C2 cancelled turn reports cancelled", turn_b.stop_reason == "cancelled", f"{turn_b.stop_reason} err={turn_b.error}")
        check("C2 cancelled turn had streamed before cancel", b_deltas_at_cancel >= 1, f"count={b_deltas_at_cancel}")
        check("C2 survivor kept streaming after the cancel", len(survivor_deltas_after_cancel) >= 3,
              f"count={len(survivor_deltas_after_cancel)}")
        check("C2 survivor was still running during the observation window", survivor_still_running)
        check("C2 survivor completed normally", turn_c.stop_reason == "end_turn", f"{turn_c.stop_reason} err={turn_c.error}")
        check("C2 survivor's bridged text equals its settled text", bool(settled_c) and settled_c == bridged_c,
              f"settled={len(settled_c)}c bridged={len(bridged_c)}c")
        check("C2 no further text for the cancelled turn after it settled",
              b_deltas_after_window == b_deltas_at_settle, f"at_settle={b_deltas_at_settle} after_window={b_deltas_after_window}")
        late = [f for f in bridge.text_deltas(session_b) if f["_received"] > b_settle_at + 0.05]
        check("C2 no late chunks timestamped after the cancelled settlement", not late, f"late={len(late)}")
        session_b_reuse = client.prompt(session_b, "hello after cancel")
        session_c_reuse = client.prompt(session_c, "hello after survive")
        check("C2 cancelled session still usable", session_b_reuse.stop_reason == "end_turn", str(session_b_reuse.error))
        check("C2 survivor session still usable", session_c_reuse.stop_reason == "end_turn", str(session_c_reuse.error))

        # --- C3 resume --------------------------------------------------
        client.close_session(session_a)
        resumed = client.resume_session(session_a)
        turn_r = client.prompt(session_a, "hello on resume")
        requests = [json.loads(line) for line in
                    (work / "mock" / "requests.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        resume_request = next((r for r in requests if (r.get("last_user") or "").startswith("hello on resume")), None)
        check("C3 resume returned config options", isinstance(resumed, dict) and "configOptions" in resumed)
        check("C3 resumed turn completed", turn_r.stop_reason == "end_turn", str(turn_r.error))
        check("C3 history replayed to the provider", bool(resume_request) and resume_request.get("n_messages", 0) >= 3,
              f"n_messages={None if not resume_request else resume_request.get('n_messages')}")

        # --- C4 approval ------------------------------------------------
        session_d = client.new_session()["sessionId"]
        turn_d = client.prompt(session_d, "escalate please")
        kinds = [o.get("kind") for o in (permission_requests[0].get("options") or [])] if permission_requests else []
        tool_calls = [u.get("update") for u in turn_d.updates if (u.get("update") or {}).get("sessionUpdate") == "tool_call"]
        check("C4 exactly one approval request", len(permission_requests) == 1, f"count={len(permission_requests)}")
        check("C4 approval offered a reject option", any(str(k).startswith("reject") for k in kinds), str(kinds))
        check("C4 turn completed after rejection", turn_d.stop_reason == "end_turn", str(turn_d.error))
        check("C4 rejected call is visible as a tool call", len(tool_calls) >= 2, f"tool_calls={len(tool_calls)}")
        check("C4 nothing written outside the workspace", not marker.exists(), str(marker))

        # --- C5 MCP attach + invoke ------------------------------------
        mcp_log = work / "mcp" / "log.jsonl"
        mcp_log.unlink(missing_ok=True)
        mcp_server = str(Path(__file__).resolve().parent / "mcp_echo_server.py")
        session_e = client.new_session(mcp_servers=[{
            "name": "spike", "command": sys.executable,
            "args": [mcp_server, "--log", str(mcp_log)], "env": [],
        }])["sessionId"]
        client.prompt(session_e, "hello")            # populates the roster
        offered = last_tools(work / "mock" / "requests.jsonl")
        echo_name = next((name for name in offered if "echo" in name.lower()), None)
        check("C5 MCP tool offered to the model", echo_name == "mcp__spike__spike_echo", str(echo_name))
        if echo_name:
            client.prompt(session_e, f'tool {echo_name} {{"text": "hello mcp"}}')
        calls = [json.loads(line) for line in mcp_log.read_text(encoding="utf-8").splitlines() if line.strip()] if mcp_log.exists() else []
        tools_call = next((c.get("params") for c in calls if c.get("method") == "tools/call"), None)
        check("C5 MCP server received tools/call", bool(tools_call), str([c.get("method") for c in calls]))
        check("C5 MCP arguments are the requested ones",
              bool(tools_call) and tools_call.get("name") == "spike_echo"
              and (tools_call.get("arguments") or {}).get("text") == "hello mcp", str(tools_call))
    finally:
        bridge.close()
        client.close()
        server.shutdown()

    # --- C6 exclusive routing (second runtime, tools disabled) ----------
    env_ex = acp_client.base_env(home, base_url_ex, bridge=(free_port(), secrets.token_hex(8)))
    client_ex = acp_client.AcpClient(args.exe, env=env_ex, cwd=str(ws),
                                     extra_args=["--patch", str(acp_patch), "--patch", str(tools_off)],
                                     stderr_path=work / "acp-exclusive-stderr.log")
    try:
        client_ex.initialize()
        mcp_log_ex = work / "mcp" / "log-exclusive.jsonl"
        mcp_log_ex.unlink(missing_ok=True)
        mcp_server = str(Path(__file__).resolve().parent / "mcp_echo_server.py")
        session_x = client_ex.new_session(mcp_servers=[{
            "name": "spike", "command": sys.executable,
            "args": [mcp_server, "--log", str(mcp_log_ex)], "env": [],
        }])["sessionId"]
        turn_x = client_ex.prompt(session_x, "hello")
        roster = last_tools(work / "mock-exclusive" / "requests.jsonl")
        bypass = sorted(name for name in roster if not name.startswith("mcp__"))
        check("C6 runtime boots with built-in tool rows disabled", turn_x.stop_reason == "end_turn", str(turn_x.error))
        check("C6 the model sees only the MCP tool", roster == ["mcp__spike__spike_echo"], str(roster))
        check("C6 no built-in execution or file tool remains", not bypass, str(bypass))
        if roster:
            client_ex.prompt(session_x, f'tool {roster[0]} {{"text": "exclusive"}}')
        calls_x = [json.loads(line) for line in mcp_log_ex.read_text(encoding="utf-8").splitlines() if line.strip()] if mcp_log_ex.exists() else []
        call_x = next((c.get("params") for c in calls_x if c.get("method") == "tools/call"), None)
        check("C6 the one exposed tool still executes", bool(call_x) and (call_x.get("arguments") or {}).get("text") == "exclusive", str(call_x))
    finally:
        client_ex.close()
        server_ex.shutdown()

    failed = [name for name, ok, _ in CHECKS if not ok]
    summary = {
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in CHECKS],
        "passed": len(CHECKS) - len(failed),
        "failed": len(failed),
        "failed_names": failed,
    }
    (work / "acceptance.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n{summary['passed']}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
