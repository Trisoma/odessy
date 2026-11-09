"""What model routes does the pinned runtime actually offer? (prompt 04 probe)

Boots the pinned runtime over ACP, opens a session, and prints the complete
``configOptions`` array — the only truthful answer to "which provider/model pairs are
mounted". Then it tries a few values through ``session/set_config_option`` and records the
runtime's own acceptance or refusal, plus the option list it returns afterwards.

    python probe_providers.py --exe <pin> --home <DSH_HOME> --work <dir> [--patch <overlay.yml>]

Evidence only; nothing here is imported by production code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acp_client  # noqa: E402
import mock_provider  # noqa: E402


def summarise(options: list[dict]) -> dict:
    """Provider -> models, walking the option's real (grouped) shape.

    Measured shape at the pin: the ``model`` select carries ``options`` whose entries are
    groups (``group``, ``name``, ``options``), and the inner entries carry ``value`` — a
    JSON ``[provider, model]`` pair. Nothing is assumed: both the group list and the pairs
    are read out of the runtime's own answer.
    """
    model = next((option for option in options if option.get("id") == "model"), None)
    effort = next((option for option in options if option.get("id") == "reasoning_effort"), None)
    grouped: dict[str, list[str]] = {}
    groups: list[str] = []
    for group in (model or {}).get("options") or []:
        label = str(group.get("group") or "?")
        groups.append(label)
        for entry in group.get("options") or []:
            try:
                provider, name = json.loads(entry.get("value"))
            except (TypeError, ValueError):
                grouped.setdefault(label, []).append("<unparseable>" + str(entry.get("value"))[:60])
                continue
            grouped.setdefault(str(provider), []).append(str(name))
    return {
        "groups": groups,
        "providers": {key: sorted(value) for key, value in sorted(grouped.items())},
        "selected": (model or {}).get("currentValue"),
        "reasoningEffort": None if effort is None else {
            "values": [entry.get("value") for entry in effort.get("options") or []],
            "current": effort.get("currentValue"),
        },
        "raw": options,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--patch", action="append", default=[])
    parser.add_argument("--try", action="append", default=[], dest="attempt",
                        help='a provider/model pair to attempt, e.g. "openai,gpt-5"')
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    for path in (work / "mock", work / "ws", home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    server, port = mock_provider.serve(work / "mock")
    env = acp_client.base_env(home, f"http://127.0.0.1:{port}")
    extra: list[str] = []
    for patch in args.patch:
        extra += ["--patch", str(Path(patch).resolve())]
    client = acp_client.AcpClient(args.exe, env=env, cwd=str(work / "ws"), extra_args=extra,
                                  stderr_path=work / "stderr.log")
    report: dict = {"patches": [str(Path(p).resolve()) for p in args.patch], "attempts": []}
    try:
        client.initialize()
        created = client.new_session()
        report["initial"] = summarise(created.get("configOptions") or [])
        session_id = created["sessionId"]
        for pair in args.attempt:
            provider, _, model = pair.partition(",")
            value = json.dumps([provider, model], separators=(",", ":"))
            try:
                result = client.set_config_option(session_id, "model", value)
                report["attempts"].append({
                    "value": [provider, model], "accepted": True,
                    "options": summarise(result.get("configOptions") or []),
                })
            except Exception as exc:  # the runtime's own refusal is the evidence
                report["attempts"].append({
                    "value": [provider, model], "accepted": False,
                    "error": str(exc)[:400],
                })
    finally:
        client.close()
        server.shutdown()

    (work / "providers.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
