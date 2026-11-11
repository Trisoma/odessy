"""Does the pinned runtime let a plugin read the tool roster, and is it the real one?

Prompt 10's remaining gap is that re-sending ``mcpServers`` proves nothing: the pin drops a
server whose command no longer starts, silently. Before relying on a roster check, this probe
establishes, against the real pin and the packaged plugin:

1. does the plugin's ``tools`` request arrive at all (feature announced, reply received)?
2. what does it report — how many agent scopes, how many global tools?
3. is that list a superset of what the *model* is offered (the mock provider records the tool
   list of every request), i.e. is it authoritative rather than a stub?
4. does it change when the attached servers change (tools off vs on)?

Only if (3) holds can a restore refuse "a tool the session had is missing" without guessing.

    python probe_tool_roster.py --exe <pinned exe> --home <DSH_HOME> --work <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "src"))

import mock_provider  # noqa: E402

from odessy.application.session import SessionController  # noqa: E402

MODEL = "mock-model"


def _profile(port: int) -> dict:
    return {"name": "custom", "adapter": "cloud", "provider": "custom",
            "model": MODEL, "endpoint": f"http://127.0.0.1:{port}/v1"}


def _requests(work: Path) -> list[dict]:
    path = work / "mock" / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _session(profile: dict) -> SessionController:
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": dict(profile)},
        selected_profile="custom",
        runtime_selection="harness",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--tool-python", default="")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    for path in (work / "mock", workspace, home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    findings: dict = {}
    server, port = mock_provider.serve(work / "mock")
    try:
        os.environ.update({
            "ODESSY_HARNESS_EXE": str(Path(args.exe).resolve()),
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
            "ODESSY_HARNESS_TOOLS": "on",
            "ODESSY_HARNESS_TOOL_PYTHON": args.tool_python or sys.executable,
        })
        profile = _profile(port)

        async def run() -> None:
            session = _session(profile)
            try:
                await asyncio.wait_for(session.run_turn("hello"), 240)
                surface = session.runtime.tool_surface
                identity = session.runtime.identity
                offered = (_requests(work)[-1].get("tools") or []) if _requests(work) else []
                findings["plugin_attached"] = session.runtime._bridge is not None
                findings["plugin_features"] = list(getattr(session.runtime._bridge, "features", ()))
                findings["surface"] = surface
                findings["identity_tool_roster"] = identity.get("tool_roster")
                findings["model_offered"] = offered
                roster = surface.get("observed") if isinstance(surface.get("observed"), list) else None
                found = [str(name) for name in (identity.get("tool_roster") or [])]
                findings["roster_is_superset_of_model_tools"] = (
                    bool(offered) and all(name in found for name in offered))
                findings["model_tools_missing_from_roster"] = [
                    name for name in offered if name not in found]
                findings["roster_tools_not_offered"] = [
                    name for name in found if offered and name not in offered][:20]
            finally:
                await session.close()

        asyncio.run(run())
    finally:
        server.shutdown()

    print(json.dumps(findings, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
