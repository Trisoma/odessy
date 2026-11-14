"""Verify which model-facing tools a set of overlays leaves in place.

Used while designing the Odessy profile: it boots the pinned runtime with one or more
patch overlays, runs a deterministic prompt, and reads the tool roster the provider
actually received. A non-empty roster names exactly what is still enabled; `--allow`
names the tools that are permitted (prompt 07 permits only `ask_user_question`).

    python verify_no_tools.py --exe <exe> --home <DSH_HOME> --work <dir> --patch <overlay.yml> [--allow <tool> ...]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import acp_client  # noqa: E402
import mock_provider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--patch", action="append", default=[])
    parser.add_argument("--allow", action="append", default=[],
                        help="a tool name the reviewed profile may still expose (prompt 07: ask_user_question)")
    parser.add_argument("--materialize-narrow", action="store_true",
                        help="append the reviewed package overlay last, as the runtime does")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    (work / "mock").mkdir(parents=True, exist_ok=True)
    (work / "ws").mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    route = work / "route.yml"
    route.write_text("- id: acp\n  config:\n    provider: deepseek-official\n    model: mock-model\n",
                     encoding="utf-8")
    server, port = mock_provider.serve(work / "mock")
    env = acp_client.base_env(home, f"http://127.0.0.1:{port}",
                              bridge=(0, "unused"))  # bridge rows need the vars only if installed
    overlays = [route, *(Path(p) for p in args.patch)]
    if args.materialize_narrow:
        from odessy.runtime_backends.harness.profile import (
            NARROW_PATCH_NAME,
            NARROW_PATCH_TEXT,
        )
        narrow = work / NARROW_PATCH_NAME
        narrow.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
        overlays.append(narrow)  # last, exactly as HarnessConfig.ordered_patches() emits it
    extra: list[str] = []
    for patch in overlays:
        extra += ["--patch", str(Path(patch).resolve())]
    client = acp_client.AcpClient(args.exe, env=env, cwd=str(work / "ws"), extra_args=extra,
                                  stderr_path=work / "stderr.log")
    try:
        client.initialize()
        session = client.new_session()["sessionId"]
        turn = client.prompt(session, "hello")
        roster = []
        log = work / "mock" / "requests.jsonl"
        if log.exists():
            lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                roster = json.loads(lines[-1]).get("tools") or []
        allowed = set(args.allow)
        print(json.dumps({
            "stop_reason": turn.stop_reason,
            "tool_roster": roster,
            "tool_count": len(roster),
            "allowed": sorted(allowed),
            "clean": turn.stop_reason == "end_turn" and set(roster) <= allowed,
        }, indent=2))
        return 0 if turn.stop_reason == "end_turn" and set(roster) <= allowed else 1
    finally:
        client.close()
        server.shutdown()
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
