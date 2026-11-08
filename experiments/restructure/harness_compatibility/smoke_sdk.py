"""Compare the pinned Python SDK transport with ACP on the same runtime pin.

Experiment only. Answers three questions with the real runtime:

  * does the SDK notification stream deliver assistant text before settlement?
  * is there any per-session cancellation method on the SDK wire at this pin?
  * what does the SDK actually expose (method probe census)?

Run:

    .harness-spike\\venv\\Scripts\\python.exe experiments\\restructure\\harness_compatibility\\smoke_sdk.py ^
        --home ..\\..\\.harness-spike\\home-sdk --work ..\\..\\.harness-spike\\work\\sdk
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_provider  # noqa: E402
from deepseek_harness import DeepSeekHarness  # noqa: E402
from deepseek_harness.errors import JsonRpcError, SdkProtocolError  # noqa: E402
from pydantic import BaseModel  # noqa: E402


class _AnyResult(BaseModel):
    """Permissive result model, so an unknown/known method can be probed by name."""

    model_config = {"extra": "allow"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()

    home = Path(args.home).resolve()
    work = Path(args.work).resolve()
    (work / "mock").mkdir(parents=True, exist_ok=True)
    (work / "ws").mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    server, port = mock_provider.serve(work / "mock")
    base_url = f"http://127.0.0.1:{port}"
    observations: dict = {"base_url": base_url}

    harness = DeepSeekHarness(
        dsh_home=str(home),
        cwd=str(work / "ws"),
        provider="deepseek-official",
        model="mock-model",
        base_url=base_url,
        api_key="spike-not-a-real-key",
        initialize_timeout_seconds=60.0,
    )
    try:
        started = time.perf_counter()
        harness.start()
        observations["start_to_ready_s"] = round(time.perf_counter() - started, 3)

        client = harness.client
        # Notification census: record every method and when assistant text appears.
        timeline: list[dict] = []
        prompt_started = time.perf_counter()

        def on_notification(notification) -> None:
            payload = notification.payload or {}
            entry = {"method": notification.method, "dt": round(time.perf_counter() - prompt_started, 4)}
            event = payload.get("event")
            if isinstance(event, dict):
                entry["event_type"] = event.get("type")
                data = event.get("data")
                if event.get("type") == "assistant/message" and isinstance(data, dict):
                    message = data.get("message") or {}
                    blocks = message.get("content") or []
                    entry["has_text"] = any(
                        isinstance(b, dict) and b.get("type") == "text" and b.get("text")
                        for b in blocks
                    )
            timeline.append(entry)

        result = harness.run("hello", session_id="spike-sdk-1", on_notification=on_notification)
        elapsed = time.perf_counter() - prompt_started
        observations["prompt"] = {
            "final_response": result.final_response,
            "finish_reason": result.finish_reason,
            "elapsed_s": round(elapsed, 3),
            "notification_count": len(timeline),
            "methods": sorted({e["method"] for e in timeline}),
            "event_types": sorted({e["event_type"] for e in timeline if e.get("event_type")}),
            "assistant_message_entries": [e for e in timeline if e.get("event_type") == "assistant/message"],
        }

        # Method probes. A JSON-RPC error means the method does not exist at this pin.
        probes = {}
        for method, params in (
            ("session/cancel", {"sessionId": "spike-sdk-1"}),
            ("session/close", {"sessionId": "spike-sdk-1"}),
            ("session/working-directory/get", {"sessionId": "spike-sdk-1"}),
        ):
            try:
                value = client.request(method, params, response_model=_AnyResult, timeout_seconds=10)
                probes[method] = {"ok": True, "result": value.model_dump()}
            except JsonRpcError as exc:
                probes[method] = {"ok": False, "code": exc.code, "message": exc.message}
            except Exception as exc:  # transport-level refusal
                probes[method] = {"ok": False, "error": repr(exc)}
        observations["method_probes"] = probes

        # Resume through the SDK wrapper (documented at this pin).
        try:
            session = harness.resume_session("spike-sdk-1")
            second = session.run("hello on resume")
            observations["resume"] = {"final_response": second.final_response, "finish_reason": second.finish_reason}
        except Exception as exc:
            observations["resume"] = {"error": repr(exc)}
    except (JsonRpcError, SdkProtocolError, TimeoutError) as exc:
        observations["fatal"] = repr(exc)
    finally:
        harness.close()
        server.shutdown()

    (work / "sdk-observations.json").write_text(json.dumps(observations, indent=2), encoding="utf-8")
    print(json.dumps(observations, indent=2)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
