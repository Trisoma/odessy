"""Ask the pinned runtime what sandbox vocabulary it actually exposes (prompt 08).

Three questions, all answered by the pinned binary rather than by a document:

1. Which profile rows implement confinement, and what configuration does each accept?
   Read from ``--dump-config`` (the composed tree) and ``--dump-config-schema`` (the
   accepted fields), so "the pin has no network vocabulary" is measured, not assumed.
2. Which session config options can a client set, and does one of them carry the sandbox
   mode? Read from ``session/new``'s ``configOptions``.
3. If such an option exists, is a value accepted and echoed back? A rejected
   ``session/set_config_option`` means the option is display-only, which would make a
   per-call pin impossible over ACP.

Everything is printed as one JSON report; nothing is inferred beyond what the pin returned.

    python probe_sandbox_vocabulary.py --exe <pinned exe> --home <DSH_HOME> --work <dir>

``--work`` must be empty or disposable: the probe writes its route overlay, mock-provider
log and stderr there.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acp_client  # noqa: E402
import mock_provider  # noqa: E402
from probe_piai_route import route_overlay  # noqa: E402

#: Row ids whose name mentions confinement, plus the policy/permission rows that decide
#: which mode reaches them. Matched by row id so a rename at a new pin shows up as absent
#: rather than as a silently empty report.
SANDBOX_ROWS = ("sandbox", "sandbox-policy", "bash-sandbox", "pwsh-sandbox", "fs-sandbox",
                "approval", "permission")


def composed_rows(exe: str, profile: str, env: dict[str, str], patches: list[str]) -> list[dict]:
    """The sandbox-related entries of the composed profile tree, with their config."""

    argv = [exe, "--profile", profile]
    for patch in patches:
        argv += ["--patch", patch]
    argv.append("--dump-config")
    dumped = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120,
                            encoding="utf-8", errors="replace")
    if dumped.returncode != 0:
        return [{"error": (dumped.stderr or "").strip()[:400]}]
    text = dumped.stdout or ""
    # The dump is a YAML entry list whose indentation depends on the layer being dumped
    # (``--dump-config`` starts an entry at column 0, ``--dump-default-config`` at column
    # 2), so the parse is relative to each entry's own indent rather than fixed offsets.
    rows: list[dict] = []
    current: dict | None = None
    entry_indent: int | None = None
    config_indent: int | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if stripped.startswith("- id: "):
            if entry_indent is None or indent <= entry_indent:
                entry_indent = indent
                current = {"id": stripped[len("- id: "):].strip(), "config": []}
                rows.append(current)
                config_indent = None
            continue
        if current is None or (entry_indent is not None and indent <= entry_indent):
            continue
        if stripped.startswith("name: ") and config_indent is None:
            current["name"] = stripped[len("name: "):].strip().strip("'")
            continue
        if stripped.startswith("config:"):
            config_indent = indent
            continue
        if config_indent is not None and indent > config_indent:
            current["config"].append(stripped)
            continue
        if config_indent is not None and indent <= config_indent:
            config_indent = None
    return [
        {"id": row["id"], "name": row.get("name"), "config": row["config"]}
        for row in rows
        if row["id"] in SANDBOX_ROWS
    ]


def schema_fields(exe: str, profile: str, env: dict[str, str]) -> dict[str, list[str]]:
    """The accepted top-level config fields of each sandbox row, from the pin's schema."""

    dumped = subprocess.run([exe, "--profile", profile, "--dump-config-schema"], env=env,
                            capture_output=True, text=True, timeout=180,
                            encoding="utf-8", errors="replace")
    if dumped.returncode != 0:
        return {"error": [(dumped.stderr or "").strip()[:400]]}
    schema = json.loads(dumped.stdout)
    entries = (schema.get("x-cordis") or {}).get("entries") or []
    definitions = schema.get("$defs") or {}
    fields: dict[str, list[str]] = {}
    for entry in entries:
        if entry.get("id") not in SANDBOX_ROWS:
            continue
        ref = str(entry.get("configRef") or "")
        definition = definitions.get(ref.rsplit("/", 1)[-1]) or {}
        properties: list[str] = []
        for branch in definition.get("anyOf") or []:
            properties.extend(sorted((branch.get("properties") or {})))
        fields[str(entry["id"])] = sorted(set(properties))
    return fields


def option_summary(options: list[dict]) -> list[dict]:
    """Every session config option the runtime offers, with the values it accepts."""

    summary = []
    for option in options:
        values: list[str] = []
        for group in option.get("options") or []:
            for entry in group.get("options") or []:
                values.append(str(entry.get("value")))
        summary.append({
            "id": option.get("id"),
            "name": option.get("name"),
            "category": option.get("category"),
            "current": option.get("currentValue"),
            "values": values,
        })
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--profile", default="acp")
    parser.add_argument("--patch", action="append", default=[],
                        help="extra overlay applied when dumping the composed tree")
    parser.add_argument("--mode-candidate", default="read-only")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    for path in (work / "mock", work / "ws", home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    server, port = mock_provider.serve(work / "mock")
    overlay = work / "probe-route.yml"
    overlay.write_text(
        route_overlay("probe-route", api="openai-completions",
                      base_url=f"http://127.0.0.1:{port}/v1",
                      models=[{"id": "probe-model", "contextWindow": 32768, "maxTokens": 1024}],
                      key_env="", display_name="Probe route"),
        encoding="utf-8",
    )
    env = acp_client.base_env(home, f"http://127.0.0.1:{port}")
    report: dict = {"exe": args.exe, "profile": args.profile}

    try:
        report["composed_rows"] = composed_rows(args.exe, args.profile, env,
                                                [str(overlay), *args.patch])
        report["schema_fields"] = schema_fields(args.exe, args.profile, env)

        client = acp_client.AcpClient(args.exe, env=env, cwd=str(work / "ws"),
                                      extra_args=["--patch", str(overlay)],
                                      stderr_path=work / "stderr.log")
        try:
            client.initialize()
            created = client.new_session()
            options = option_summary(created.get("configOptions") or [])
            report["config_options"] = options
            report["set_attempts"] = []
            session_id = created.get("sessionId")
            for option in options:
                if option["id"] == "model":
                    continue
                attempt: dict = {"id": option["id"], "requested": args.mode_candidate}
                try:
                    result = client.set_config_option(session_id, str(option["id"]),
                                                      args.mode_candidate)
                    after = option_summary(result.get("configOptions") or [])
                    now = next((item["current"] for item in after if item["id"] == option["id"]),
                               None)
                    attempt.update({"accepted": True, "current_after": now})
                except Exception as exc:  # the pin's error text is the evidence
                    attempt.update({"accepted": False, "error": str(exc)[:400]})
                report["set_attempts"].append(attempt)
        finally:
            client.close()
    finally:
        server.shutdown()

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
