"""Prompt 11 smoke: an explicit participant consultation on the pinned runtime.

What this proves that unit tests cannot: the *real* ACP child, composed from the pinned
executable, accepts the framed prompt and answers it. The consultation itself is Odessy's
(one advice-only call to the participant's own provider) — this run does not give the
runtime a second model or a tool surface, so a passing run means the frame survived the
real provider wire and the turn settled normally.

Usage:
    python smoke_p11.py --exe <pinned runtime exe> --home <fresh dsh home> --work <work dir>
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mock_provider  # noqa: E402
from smoke_p10 import _processes, _profile, _requests  # noqa: E402

from odessy.application.durable import SessionStore  # noqa: E402
from odessy.application.session import SessionController, consultation_prompt  # noqa: E402
from odessy.collaboration.config import (  # noqa: E402
    CollaborationConfig, CollaborationSettings, ParticipantProfile,
)
from odessy.collaboration.participants import CollaborationService  # noqa: E402

ADVICE = "The second option is cheaper and reversible."
QUESTION = "consult reviewer: which option should I take?"


def _collaboration(*, max_consultations: int = 1) -> CollaborationService:
    config = CollaborationConfig(
        CollaborationSettings(enabled=True, max_consultations=max_consultations),
        {"reviewer": ParticipantProfile("reviewer", "custom_agent", model="fixture")})
    # A recorded adapter stands in for the participant's provider. The frame under test is
    # Odessy's; the participant's transport is a separate, already-tested component.
    return CollaborationService(config, adapters={
        "reviewer": lambda profile, question, context: ADVICE})


def _session(store: SessionStore, profile: dict, collaboration) -> SessionController:
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": dict(profile)},
        selected_profile="custom",
        runtime_selection="harness",
        durable=store,
        collaboration=collaboration,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    state = work / "state"
    for path in (work / "mock", workspace, home, state):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed), detail))

    before = set(_processes("deepseek-harness-sdk-runtime"))
    server, port = mock_provider.serve(work / "mock")
    try:
        os.environ.update({
            "ODESSY_HARNESS_EXE": str(Path(args.exe).resolve()),
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
            # No tool surface: this scenario is about the turn, not about tools.
            "ODESSY_HARNESS_TOOLS": "off",
            "ODESSY_SESSION_STATE_DIR": str(state),
        })
        profile = _profile(port)
        store = SessionStore(state)
        findings: dict = {}

        async def scenario() -> None:
            session = _session(store, profile, _collaboration())
            try:
                result = await asyncio.wait_for(
                    session.run_turn(QUESTION, participant="reviewer"), 240)
                findings["final_text"] = result.final_text
                findings["events"] = [
                    {"type": event["type"], "data": event["data"]}
                    for event in session.events if event["type"] in
                    {"transcript", "consultation", "result", "error"}]
                record = dict(session.record or {})
                findings["turns"] = [turn.get("status") for turn in record.get("turns") or []]
            finally:
                await session.close()

        asyncio.run(scenario())
        requests = _requests(work)
        findings["requests"] = len(requests)
        (work / "findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")

        got = requests[0] if requests else {}
        expected = consultation_prompt("reviewer", {"status": "ok", "answer": ADVICE}, QUESTION)
        check("the pinned runtime made exactly one model request", len(requests) == 1,
              str(len(requests)))
        check("the engine received exactly the framed prompt",
              got.get("last_user_sha256") == hashlib.sha256(expected.encode("utf-8")).hexdigest(),
              f"length={got.get('last_user_length')}")
        check("the engine received the consultation frame",
              str(got.get("last_user") or "").startswith("[Odessy consultation"),
              str(got.get("last_user"))[:80])
        check("the request itself is still the turn's tail",
              str(got.get("last_user_tail") or "").endswith(QUESTION),
              str(got.get("last_user_tail"))[-80:])
        check("the turn settled with the runtime's answer",
              bool(findings.get("final_text")), str(findings.get("final_text"))[:120])
        transcript = [event for event in findings.get("events", [])
                      if event["type"] == "transcript"]
        check("the client transcript keeps the user's own words",
              bool(transcript) and transcript[0]["data"].get("text") == QUESTION,
              str(transcript[:2])[:160])
        consultation = [event for event in findings.get("events", [])
                        if event["type"] == "consultation"]
        check("one consultation event names the participant",
              len(consultation) == 1
              and consultation[0]["data"].get("participant") == "reviewer",
              str(consultation)[:160])
        check("no error event was emitted",
              not any(event["type"] == "error" for event in findings.get("events", [])), "")
        check("the durable turn settled", findings.get("turns") == ["settled"],
              str(findings.get("turns")))
        leaked = sorted(set(_processes("deepseek-harness-sdk-runtime")) - before)
        check("the runtime child is gone", not leaked, str(leaked))
    finally:
        server.shutdown()

    passed = sum(1 for _, ok, _ in checks if ok)
    print(json.dumps({"checks": [{"check": name, "passed": ok, "detail": detail}
                                 for name, ok, detail in checks],
                      "summary": f"{passed}/{len(checks)} checks passed"},
                     indent=2, ensure_ascii=False))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
