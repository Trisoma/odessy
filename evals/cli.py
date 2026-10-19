"""Command line: ``python -m evals run|list|summarize|compare``."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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

_REGRADE_HELP = "Grade the recorded attempts again with the current graders (attempts.jsonl is not changed)."

EVAL_DEPLOYMENT_ENV = {
    "ODESSY_DEPLOYMENT": "split",
    "ODESSY_LOCAL_OS": "linux",
    "ODESSY_LAPTOP_OS": "windows",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description="Odessy agent evaluation suite.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run the cases against one model configuration.")
    run.add_argument(
        "--backend",
        required=True,
        help="Model backend, as for `python -m odessy --model-backend`: lfm, ollama, claude, openai, deepseek, qwen, kimi, custom.",
    )
    run.add_argument("--repeat", type=int, default=3, help="Attempts per case (default 3).")
    run.add_argument(
        "--attempt-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Backstop for awaitable work in one attempt (default 1800; 0 disables). "
            "Cannot interrupt synchronous inference; local streams have a separate budget."
        ),
    )
    run.add_argument(
        "--max-model-calls",
        type=int,
        default=None,
        help="Model calls allowed per turn, as ODESSY_MAX_MODEL_CALLS in the CLI "
        "(default: the CLI's default, 8 for cloud backends and 4 for local ones).",
    )
    run.add_argument(
        "--max-infra-errors",
        type=int,
        default=5,
        help="Stop the run after this many infrastructure errors in a row (default 5; 0 never stops).",
    )
    run.add_argument("--category", action="append", choices=CATEGORIES, help="Only these categories (repeatable).")
    run.add_argument("--case", action="append", dest="case_ids", help="Only these case ids (repeatable).")
    run.add_argument("--label", help="Name for this run in reports (default: the backend).")
    run.add_argument("--output-dir", type=Path, help="Where to write results (default: evals/results/<time>-<label>).")

    commands.add_parser("list", help="List the cases.")

    summarize = commands.add_parser("summarize", help="Regenerate summary.md/json for a run directory.")
    summarize.add_argument("run_dir", type=Path)
    summarize.add_argument("--regrade", action="store_true", help=_REGRADE_HELP)

    compare = commands.add_parser("compare", help="Compare several run directories side by side.")
    compare.add_argument("run_dirs", type=Path, nargs="+")
    compare.add_argument("--output", type=Path, help="Write the comparison here as well as printing it.")
    compare.add_argument("--regrade", action="store_true", help=_REGRADE_HELP)

    args = parser.parse_args(argv)
    if args.command == "list":
        return _list()
    if args.command == "summarize":
        from evals.report import write_summary

        write_summary(args.run_dir, regrade_records=args.regrade)
        print((args.run_dir / "summary.md").read_text(encoding="utf-8"))
        return 0
    if args.command == "compare":
        return _compare(args.run_dirs, args.output, regrade=args.regrade)
    return asyncio.run(_run(args))


def _list() -> int:
    for case in load_cases():
        print(f"{case.category:10s} {case.id:36s} {case.prompt}")
    return 0


def _compare(run_dirs: list[Path], output: Path | None, *, regrade: bool = False) -> int:
    from evals.report import render_comparison, write_summary

    runs = []
    for run_dir in run_dirs:
        summary = write_summary(run_dir, regrade_records=regrade)
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

    from evals.graders import grade
    from evals.harness import (
        DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
        run_attempt,
    )
    from evals.report import write_summary

    if args.repeat < 1:
        raise SystemExit("--repeat must be at least 1")
    if args.max_model_calls is not None and args.max_model_calls < 1:
        raise SystemExit("--max-model-calls must be at least 1")
    if args.max_infra_errors < 0:
        raise SystemExit("--max-infra-errors must be at least 0")
    if args.attempt_timeout is not None and args.attempt_timeout < 0:
        raise SystemExit("--attempt-timeout must be at least 0")
    load_config_env()
    # The cases describe the two-machine layout: "this machine" is the Linux Jetson and
    # "my laptop" is Windows. Pin it, so the model sees the same tools and the same
    # environment sentence whichever machine runs the evaluation.
    os.environ.update(EVAL_DEPLOYMENT_ENV)
    cases = _select(load_cases(), args.category, args.case_ids)
    if not cases:
        raise SystemExit("no cases selected")

    model = _build_model(False, backend=args.backend)
    attempt_timeout = args.attempt_timeout
    if attempt_timeout is None:
        attempt_timeout = DEFAULT_ATTEMPT_TIMEOUT_SECONDS
    attempt_timeout = attempt_timeout or None  # 0 disables the backstop
    if args.max_model_calls is None:
        # Evaluate what the product does by default for this backend.
        from odessy.cli import default_max_model_calls
        from odessy.cloud_model import CloudModel

        args.max_model_calls = default_max_model_calls(cloud=isinstance(model, CloudModel))
    label = args.label or args.backend
    started = datetime.now(timezone.utc)
    run_dir = args.output_dir or RESULTS_DIR / f"{started.strftime('%Y%m%d-%H%M%S')}-{label}"
    run_dir.mkdir(parents=True, exist_ok=False)

    git_commit, git_commit_note = _git_commit()
    metadata = {
        "label": label,
        "backend": args.backend,
        "model": getattr(model, "model", type(model).__name__),
        "deployment": dict(EVAL_DEPLOYMENT_ENV),
        "repeat": args.repeat,
        "attempt_timeout_seconds": attempt_timeout,
        "max_model_calls": args.max_model_calls,
        "cases": [case.id for case in cases],
        "odessy_version": _odessy_version(),
        "git_commit": git_commit,
        "git_commit_note": git_commit_note,
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()}), Python {platform.python_version()}",
        "started_at": started.isoformat(),
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    total = len(cases) * args.repeat
    done = 0
    infra_streak = 0
    aborted: str | None = None
    with (run_dir / "attempts.jsonl").open("a", encoding="utf-8") as handle:
        for case in cases:
            if aborted:
                break
            for attempt_index in range(1, args.repeat + 1):
                attempt = await run_attempt(
                    case, model, attempt_index,
                    timeout_seconds=attempt_timeout,
                    max_model_calls=args.max_model_calls,
                )
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
                infra_streak = infra_streak + 1 if attempt.outcome == "infra_error" else 0
                if args.max_infra_errors and infra_streak >= args.max_infra_errors:
                    aborted = (
                        f"stopped after {infra_streak} infrastructure errors in a row "
                        f"(last: {attempt.error}); rerun the remaining cases with --case or --category"
                    )
                    print(f"\n{aborted}", flush=True)
                    break

    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    if aborted:
        metadata["aborted"] = {"reason": aborted, "attempts_completed": done}
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    summary = write_summary(run_dir)
    print()
    print((run_dir / "summary.md").read_text(encoding="utf-8").split("## Tools")[0])
    if aborted:
        print(f"ABORTED after {done} of {total} attempts: {aborted}")
        print("Reported rates cover only recorded attempts, not the full suite.")
    print(f"Results written to {run_dir}")
    if summary["safety"]["forbidden_executed"]:
        return 1
    return 2 if aborted else 0


def _odessy_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("odessy")
    except Exception:  # noqa: BLE001 - not installed as a package
        return None


def _git_commit() -> tuple[str | None, str | None]:
    if commit := os.environ.get("ODESSY_COMMIT"):
        return commit, "from ODESSY_COMMIT"
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        if not commit:
            raise ValueError("git rev-parse returned an empty commit")
        return commit, None
    except Exception as exc:  # noqa: BLE001 - preserve provenance diagnostics
        note = f"git rev-parse failed in {root}: {type(exc).__name__}: {exc}"
        if isinstance(exc, FileNotFoundError):
            note += "; git not on PATH"
        if not (root / ".git").exists():
            note += f"; no .git in {root}"
        if stderr := getattr(exc, "stderr", None):
            note += f"; stderr: {stderr.strip()}"
    try:
        sources = sorted((root / "src" / "odessy").rglob("*.py"), key=lambda path: path.as_posix())
        if not sources:
            raise FileNotFoundError(f"no Python sources in {root / 'src' / 'odessy'}")
        digest = hashlib.sha256()
        for path in sources:
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes() + b"\0")
        return None, f"{note}; tree-sha256: {digest.hexdigest()[:12]} (not a git commit)"
    except Exception as exc:  # noqa: BLE001 - evals can run without source files
        return None, f"{note}; tree hash failed: {type(exc).__name__}: {exc}"


if __name__ == "__main__":
    sys.exit(main())
