"""Can an Odessy route be expressed as a hand-declared pi-ai route at the pin? (prompt 04)

Writes a one-row overlay that adds a provider to ``llm-pi-ai``, boots the pinned runtime,
prints the model catalogue it advertises, selects ``[route, model]``, runs one prompt and
reports: whether the selection was accepted, which ``session/update`` kinds arrived, and
whether the mock endpoint actually received the request with the injected credential.

    python probe_piai_route.py --exe <pin> --home <DSH_HOME> --work <dir> \
        --route mock-openai --api openai-completions --model mock-model --key-env ODESSY_PROVIDER_API_KEY

The endpoint is always the local deterministic fixture; no real provider is contacted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acp_client  # noqa: E402
import mock_provider  # noqa: E402


def route_overlay(route: str, *, api: str, base_url: str, models: list[dict], key_env: str,
                  display_name: str, extra: dict | None = None) -> str:
    """The narrow overlay that declares exactly one pi-ai provider route."""
    profile: dict = {"displayName": display_name, "api": api, "baseURL": base_url,
                     "models": models}
    if key_env:
        profile["apiKeyEnv"] = key_env
    if extra:
        profile.update(extra)
    config = {"providers": {route: profile}}
    # JSON is valid YAML 1.2, so a flow mapping is a safe way to emit this block without a
    # YAML dependency in the experiment.
    return ("# Probe: one hand-declared pi-ai route (prompt 04)\n"
            "- id: llm-pi-ai\n"
            f"  config: {json.dumps(config, ensure_ascii=False)}\n")


def update_kinds(updates: list[dict]) -> list[str]:
    kinds = []
    for message in updates:
        update = message.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind:
            kinds.append(kind)
    return sorted(set(kinds))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--api", default="openai-completions")
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-env", default="ODESSY_PROVIDER_API_KEY")
    parser.add_argument("--key-value", default="mock-key-not-a-secret")
    parser.add_argument("--context-window", type=int, default=32768)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--prompt", default="hello")
    parser.add_argument("--no-key", action="store_true", help="declare the route without a key reference")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    for path in (work / "mock", work / "ws", home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    server, port = mock_provider.serve(work / "mock")
    overlay = work / "pi-ai-route.yml"
    overlay.write_text(
        route_overlay(
            args.route, api=args.api,
            base_url=f"http://127.0.0.1:{port}/v1",
            models=[{"id": args.model, "contextWindow": args.context_window,
                     "maxTokens": args.max_tokens}],
            key_env="" if args.no_key else args.key_env,
            display_name=args.route.replace("-", " ").title(),
        ),
        encoding="utf-8",
    )
    env = acp_client.base_env(home, f"http://127.0.0.1:{port}")
    if not args.no_key:
        env[args.key_env] = args.key_value
    client = acp_client.AcpClient(args.exe, env=env, cwd=str(work / "ws"),
                                  extra_args=["--patch", str(overlay)],
                                  stderr_path=work / "stderr.log")
    report: dict = {"route": args.route, "api": args.api, "model": args.model}
    try:
        client.initialize()
        created = client.new_session()
        options = created.get("configOptions") or []
        model_option = next((option for option in options if option.get("id") == "model"), None)
        report["groups"] = [group.get("group") for group in (model_option or {}).get("options") or []]
        pairs = []
        for group in (model_option or {}).get("options") or []:
            for entry in group.get("options") or []:
                pairs.append(entry.get("value"))
        report["pairs"] = pairs
        session_id = created["sessionId"]
        value = json.dumps([args.route, args.model], separators=(",", ":"))
        try:
            client.set_config_option(session_id, "model", value)
            report["selected"] = True
        except Exception as exc:
            report["selected"] = False
            report["select_error"] = str(exc)[:400]
        if report["selected"]:
            turn = client.prompt(session_id, args.prompt, timeout=90)
            report["stop_reason"] = turn.stop_reason
            report["error"] = None if turn.error is None else str(turn.error)[:400]
            report["updates"] = update_kinds(turn.updates)
            report["raw_updates"] = [entry.get("update") for entry in turn.updates][:8]
            report["prompt_result"] = turn.result
    finally:
        client.close()
        server.shutdown()

    log = work / "mock" / "requests.jsonl"
    requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    report["provider_requests"] = len(requests)
    report["provider_models"] = [entry.get("model") for entry in requests]
    report["provider_paths"] = sorted({entry.get("path") for entry in requests})
    report["authorization_present"] = bool(requests) and all(item.get("authorization_present") is True for item in requests)
    (work / "piai.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report.get("selected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
