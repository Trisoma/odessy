"""Command line: ``python -m evals run|list|summarize|compare``."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.definitions import CATEGORIES, Case, load_cases

RESULTS_DIR = Path(__file__).parent / "results"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description="Odessy agent evaluation suite.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run the cases against one model configuration.")
    run.add_argument(
        "--backend",
        required=True,
        help="Model backend, as for `python -m odessy --model-backend`: lfm, ollama, claude, openai, deepseek, qwen, kimi, custom.",
    )
    run.add_argument(
        "--routing",
        choices=["on", "off"],
        default="on",
        help="Keyword routing for local backends (default on). Cloud backends never use it.",
    )
    run.add_argument("--repeat", type=int, default=3, help="Attempts per case (default 3).")
    run.add_argument("--category", action="append", choices=CATEGORIES, help="Only these categories (repeatable).")
    run.add_argument("--case", action="append", dest="case_ids", help="Only these case ids (repeatable).")
    run.add_argument("--label", help="Name for this run in reports (default: backend + routing).")
    run.add_argument("--output-dir", type=Path, help="Where to write results (default: evals/results/<time>-<label>).")

    commands.add_parser("list", help="List the cases.")

    summarize = commands.add_parser("summarize", help="Regenerate summary.md/json for a run directory.")
    summarize.add_argument("run_dir", type=Path)

    compare = commands.add_parser("compare", help="Compare several run directories side by side.")
    compare.add_argument("run_dirs", type=Path, nargs="+")
    compare.add_argument("--output", type=Path, help="Write the comparison here as well as printing it.")

    args = parser.parse_args(argv)
    if args.command == "list":
        return _list()
    if args.command == "summarize":
        from evals.report import write_summary

        write_summary(args.run_dir)
        print((args.run_dir / "summary.md").read_text(encoding="utf-8"))
        return 0
    if args.command == "compare":
        return _compare(args.run_dirs, args.output)
    return asyncio.run(_run(args))


def _list() -> int:
    for case in load_cases():
        print(f"{case.category:10s} {case.id:36s} {case.prompt}")
    return 0


def _compare(run_dirs: list[Path], output: Path | None) -> int:
    from evals.report import render_comparison, write_summary

    runs = []
    for run_dir in run_dirs:
        summary = write_summary(run_dir)
        metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        runs.append((metadata.get("label", run_dir.name), summary))
    text = render_comparison(runs)
    print(text)
    if output is not None:
        output.write_text(text, encoding="utf-8")
    return 0


def _select(cases: list[Case], categories: list[str] | None, case_ids: list[str] | None) -> list[Case]:
    if case_ids:
        unknown = set(case_ids) - {case.id for case in cases}
        if unknown:
            raise SystemExit(f"unknown case ids: {sorted(unknown)}")
        cases = [case for case in cases if case.id in case_ids]
    if categories:
        cases = [case for case in cases if case.category in categories]
    return cases


async def _run(args: argparse.Namespace) -> int:
    # Imported here so `list`/`compare` work without the model stack configured.
    from odessy.cli import _build_model
    from odessy.config_file import load_config_env
    from odessy.ollama_model import KEYWORD_ROUTING_ENV

    from evals.graders import grade
    from evals.harness import run_attempt
    from evals.report import write_summary

    if args.repeat < 1:
        raise SystemExit("--repeat must be at least 1")
    load_config_env()
    os.environ[KEYWORD_ROUTING_ENV] = args.routing
    cases = _select(load_cases(), args.category, args.case_ids)
    if not cases:
        raise SystemExit("no cases selected")

    model = _build_model(False, backend=args.backend)
    label = args.label or f"{args.backend}-routing-{args.routing}"
    started = datetime.now(timezone.utc)
    run_dir = args.output_dir or RESULTS_DIR / f"{started.strftime('%Y%m%d-%H%M%S')}-{label}"
    run_dir.mkdir(parents=True, exist_ok=False)

    metadata = {
        "label": label,
        "backend": args.backend,
        "model": getattr(model, "model", type(model).__name__),
        "keyword_routing": args.routing,
        "repeat": args.repeat,
        "cases": [case.id for case in cases],
        "odessy_version": _odessy_version(),
        "git_commit": _git_commit(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()}), Python {platform.python_version()}",
        "started_at": started.isoformat(),
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    total = len(cases) * args.repeat
    done = 0
    with (run_dir / "attempts.jsonl").open("a", encoding="utf-8") as handle:
        for case in cases:
            for attempt_index in range(1, args.repeat + 1):
                attempt = await run_attempt(case, model, attempt_index)
                result = grade(case, attempt)
                record: dict[str, Any] = {**attempt.to_dict(), "prompt": case.prompt, "grade": result.to_dict()}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                done += 1
                verdict = "SKIP" if result.excluded else ("PASS" if result.passed else "FAIL")
                print(
                    f"[{done:3d}/{total}] {verdict} {case.id} #{attempt_index} "
                    f"({attempt.outcome}, {attempt.latency_ms / 1000:.1f}s)",
                    flush=True,
                )

    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    summary = write_summary(run_dir)
    print()
    print((run_dir / "summary.md").read_text(encoding="utf-8").split("## Tools")[0])
    print(f"Results written to {run_dir}")
    return 0 if summary["safety"]["forbidden_executed"] == 0 else 1


def _odessy_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("odessy")
    except Exception:  # noqa: BLE001 - not installed as a package
        return None


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - not a git checkout
        return None


if __name__ == "__main__":
    sys.exit(main())
