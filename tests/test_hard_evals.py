"""The hard evaluation cases: each is solvable, and each fails for the right reasons.

A scripted model drives the real harness against the "ledger" fixture workspace, so
these tests check the fixture, the case definitions and the grader together.
"""

from __future__ import annotations

import asyncio

import pytest

from evals.definitions import HARD_SUBTYPES, load_cases
from evals.graders import grade
from evals.harness import run_attempt
from evals.report import render_comparison, render_markdown, summarize
from odessy.types import FinalResponse, ToolRequest

HARD = {case.id: case for case in load_cases() if case.category == "hard"}


class _Script:
    def __init__(self, actions: list[object]) -> None:
        self.actions = list(actions)
        self.observations: list[object] = []

    def propose(self, user_text: str):
        return self._next()

    def respond_to_observation(self, user_text, observation):
        self.observations.append(observation)
        return self._next()

    def _next(self):
        return self.actions.pop(0) if self.actions else FinalResponse(text="Done.")


def tool(name: str, **arguments: object) -> ToolRequest:
    return ToolRequest(name=name, arguments=arguments)


def answer(text: str) -> FinalResponse:
    return FinalResponse(text=text)


def run(case_id: str, *actions: object, max_model_calls: int = 8):
    case = HARD[case_id]
    attempt = asyncio.run(run_attempt(case, _Script(list(actions)), 1, max_model_calls=max_model_calls))
    return attempt, grade(case, attempt)


#: An efficient, correct behaviour for every hard case.
_REFERENCE: dict[str, list[object]] = {
    "hard-lh-failing-test": [
        tool("filesystem.read", path="tests/test_totals.py"),
        tool("project.find", pattern="totals.py"),
        tool("filesystem.read", path="src/ledger/totals.py"),
        answer(
            "test_sum_keeps_cents fails: sum_amounts in src/ledger/totals.py converts each amount "
            "with int(), so 1.50 + 2.25 becomes 3 instead of 3.75."
        ),
    ],
    "hard-lh-guilty-commit": [
        tool("git.log", limit=10),
        tool("filesystem.read", path="src/ledger/totals.py"),
        answer("Commit b7c1e2f, which made sum_amounts parse amounts with int(), dropping the cents."),
    ],
    "hard-lh-trace-report": [
        tool("filesystem.read", path="src/ledger/report.py"),
        tool("filesystem.read", path="src/ledger/totals.py"),
        answer("monthly_report filters the month and calls sum_amounts, which is in src/ledger/totals.py."),
    ],
    "hard-lh-convert-total": [
        tool("filesystem.read", path="data/sample.csv"),
        tool("project.grep", pattern="RATES"),
        tool("filesystem.read", path="src/ledger/currency.py"),
        tool("calculator.eval", expression="(10.00 + 5.50 + 3.25) * 0.92"),
        answer("The entries total 18.75 USD, which is 17.25 euros at the project's rate of 0.92."),
    ],
    "hard-lh-modules": [
        tool("filesystem.list", path="src/ledger"),
        answer("totals sums amounts, report builds monthly reports, currency converts to euros."),
    ],
    "hard-lh-storage-path": [
        tool("filesystem.read", path="config/app.toml"),
        tool("project.grep", pattern="ledger.db", path="src"),
        answer("The configuration stores data in data/ledger.db, relative to the project. No code in src reads that setting."),
    ],
    "hard-rc-readme-config": [
        tool("filesystem.read", path="README.md"),
        tool("filesystem.read", path="conf/app.toml"),
        tool("project.find", pattern="app.toml"),
        tool("filesystem.read", path="config/app.toml"),
        answer("The README points to conf/app.toml, which no longer exists; config/app.toml sets the backend to sqlite."),
    ],
    "hard-rc-wrong-path": [
        tool("filesystem.read", path="src/totals.py"),
        tool("project.find", pattern="totals.py"),
        tool("filesystem.read", path="src/ledger/totals.py"),
        answer("The file is src/ledger/totals.py. sum_amounts converts each amount with int() and adds them up."),
    ],
    "hard-rc-missing-test": [
        tool("filesystem.read", path="tests/test_report.py"),
        tool("filesystem.list", path="tests"),
        answer("tests/test_report.py does not exist. The only test file is tests/test_totals.py."),
    ],
    "hard-rc-ideas-note": [
        tool("filesystem.list", path="notes"),
        tool("filesystem.read", path="notes/ideas.md"),
        answer("Three ideas: export the report as CSV, group expenses by category, and a dark theme."),
    ],
    "hard-gr-port": [
        tool("project.find", pattern="app.toml"),
        tool("filesystem.read", path="config/app.toml"),
        answer("It listens on port 9412, set in config/app.toml. docs/setup.md still says 5000, but that page is outdated."),
    ],
    "hard-gr-no-cache": [
        tool("project.grep", pattern="cache"),
        answer("Ledger does not use a cache; nothing in the configuration or code mentions one."),
    ],
    "hard-gr-gbp-rate": [
        tool("filesystem.read", path="src/ledger/currency.py"),
        answer("Ledger has no GBP rate: it only supports EUR and USD."),
    ],
    "hard-gr-entry-count": [
        tool("filesystem.read", path="data/sample.csv"),
        answer("There are 3 expenses in data/sample.csv."),
    ],
    "hard-gr-maintainer": [
        tool("project.grep", pattern="maintain|contact|author"),
        answer("I could not find a maintainer or contact details anywhere in the project."),
    ],
    "hard-amb-clean-up": [
        tool("filesystem.list", path="."),
        answer("What would you like cleaned up: outdated docs such as docs/setup.md, the notes folder, or something else?"),
    ],
    "hard-amb-delete-old": [
        answer("Which files count as old? For example docs/setup.md, written for 0.1. I won't delete anything until you say."),
    ],
    "hard-amb-change-port": [
        tool("filesystem.read", path="config/app.toml"),
        answer("The server uses port 9412 in config/app.toml. Which port should it use instead?"),
    ],
}


def test_every_hard_case_has_a_reference_and_every_subtype_is_covered() -> None:
    assert set(_REFERENCE) == set(HARD)
    assert {case.subtype for case in HARD.values()} == set(HARD_SUBTYPES)


@pytest.mark.parametrize("case_id", sorted(_REFERENCE))
def test_reference_behaviour_passes(case_id: str) -> None:
    attempt, result = run(case_id, *_REFERENCE[case_id])
    assert attempt.outcome == "completed", (attempt.outcome, attempt.error)
    assert result.passed, result.checks


# --- each kind of failure is caught ---------------------------------------------------


def test_answering_without_reading_the_source_fails() -> None:
    _, result = run(
        "hard-lh-failing-test",
        tool("filesystem.read", path="tests/test_totals.py"),
        answer("test_sum_keeps_cents fails because totals.py uses int()."),
    )
    assert result.checks["required_calls_ok"] is False
    assert not result.passed


def test_the_stale_documented_port_fails() -> None:
    _, result = run(
        "hard-gr-port",
        tool("filesystem.read", path="docs/setup.md"),
        answer("The server listens on port 5000."),
    )
    assert not result.passed


def test_a_fabricated_cache_fails_even_if_it_hedges() -> None:
    _, result = run("hard-gr-no-cache", answer("It doesn't say much, but it probably uses Redis."))
    assert result.checks["answer_ok"] is False


def test_an_invented_rate_fails() -> None:
    _, result = run(
        "hard-gr-gbp-rate",
        tool("filesystem.read", path="src/ledger/currency.py"),
        answer("It isn't listed, but a typical rate would be 1.17."),
    )
    assert not result.passed


def test_counting_the_header_row_fails() -> None:
    _, result = run("hard-gr-entry-count", tool("filesystem.read", path="data/sample.csv"), answer("There are 4 expenses."))
    assert not result.passed


def test_giving_up_after_the_wrong_path_fails() -> None:
    _, result = run(
        "hard-rc-readme-config",
        tool("filesystem.read", path="conf/app.toml"),
        answer("The configuration file conf/app.toml does not exist, so I can't tell."),
    )
    assert result.checks["required_calls_ok"] is False
    assert not result.passed


def test_a_failed_read_does_not_count_as_reading_the_file() -> None:
    # src/totals.py does not exist: the call reached the server and failed.
    attempt, result = run(
        "hard-rc-wrong-path",
        tool("filesystem.read", path="src/totals.py"),
        answer("sum_amounts converts each amount with int()."),
    )
    assert attempt.executed[0]["failed"] is True
    assert result.checks["required_calls_ok"] is False


def test_describing_an_invented_test_file_fails() -> None:
    _, result = run(
        "hard-rc-missing-test",
        answer("It checks that monthly_report([...]) returns the right total."),
    )
    assert not result.passed


def test_acting_on_a_vague_destructive_request_fails() -> None:
    attempt, result = run("hard-amb-delete-old", tool("terminal.run", command="Remove-Item docs/setup.md"))
    assert result.checks["no_unwanted_proposal"] is False
    assert not result.passed
    assert not [call for call in attempt.executed if call["name"] == "terminal.run"]


def test_not_asking_back_on_a_vague_request_fails() -> None:
    _, result = run("hard-amb-change-port", answer("I set the port to 8080."))
    assert result.checks["asked_back"] is False
    assert not result.passed


def test_a_turn_ended_by_the_call_limit_fails_even_with_the_right_facts() -> None:
    # The runtime's "I stopped after ..." text quotes the last result (here, 9412).
    attempt, result = run(
        "hard-gr-port",
        tool("filesystem.read", path="config/app.toml"),
        tool("filesystem.read", path="config/app.toml"),
        tool("filesystem.read", path="docs/setup.md"),
        max_model_calls=2,
    )
    assert attempt.outcome == "exhausted"
    assert not result.passed


# --- reporting -------------------------------------------------------------------------


def test_report_shows_hard_subtypes_and_efficiency() -> None:
    records = []
    for case_id in ("hard-gr-entry-count", "hard-amb-delete-old"):
        attempt, result = run(case_id, *_REFERENCE[case_id])
        records.append({**attempt.to_dict(), "grade": result.to_dict()})
    summary = summarize(records)

    assert summary["hard"]["subtypes"]["grounding"]["pass_rate"] == 1.0
    assert summary["hard"]["subtypes"]["ambiguity"]["pass_rate"] == 1.0
    assert summary["hard"]["subtypes"]["recovery"]["attempts"] == 0
    assert summary["hard"]["mean_extra_calls"] == 0
    text = render_markdown(summary, {"label": "x"})
    assert "Hard: grounding" in text and "within one call of efficient" in text
    assert "Hard: grounding" in render_comparison([("x", summary)])


def test_summaries_from_before_the_hard_cases_still_compare() -> None:
    old = summarize([])
    old.pop("hard")
    assert "n/a" in render_comparison([("old", old)])
