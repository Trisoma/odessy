"""The live evaluation's own logic, with fake agents. No coding CLI runs here."""
import asyncio
from pathlib import Path

from evals import handoff_live
from evals.handoff_cases import FEATURES, LEDGER, REVIEWS, hidden_test


def output(text):
    return {'returncode': 0, 'output': text}


def test_a_document_run_is_graded_by_the_hidden_test(isolated_patch_repo):
    async def runner(command, *, cwd, env, on_output):
        if 'implement' in ' '.join(command).lower():
            path = Path(cwd) / 'ledger.py'
            path.write_text(path.read_text() + '\n\ndef balance_on(entries, day):\n'
                            '    return sum(e.amount_cents for e in entries if e.day <= day)\n')
        return output('The document')
    async def run():
        repo = handoff_live.make_repository(isolated_patch_repo)
        result = await handoff_live.case_document_to_code('a1-balance-on', repo, 30, runner)
        assert result['state'] == 'completed' and result['delivered'] and result['passed'], result
    asyncio.run(run())


def test_a_review_counts_only_when_it_names_the_defect(isolated_patch_repo):
    replies = iter(['nothing here\nVERDICT: DEFECT', 'uses <= instead of <\nVERDICT: DEFECT'])
    async def run():
        for found in (False, True):
            reply = next(replies)
            async def runner(command, *, cwd, env, on_output):
                if 'ledger.py' in command[-1] and command[-1].startswith('{'):
                    (Path(cwd) / 'ledger.py').write_text(LEDGER + '\n# change\n')
                    return output('')
                return output(reply)
            repo = handoff_live.make_repository(isolated_patch_repo / str(found))
            result = await handoff_live.case_review('b1-boundary', repo, 30, runner)
            assert (result['flagged'], result['found']) == (True, found), result
    (isolated_patch_repo / 'False').mkdir()
    (isolated_patch_repo / 'True').mkdir()
    asyncio.run(run())


def test_a_tampered_worktree_counts_as_a_refused_handoff(isolated_patch_repo):
    # With the real subprocess runner: the scripted first step needs no agent.
    async def run():
        repo = handoff_live.make_repository(isolated_patch_repo)
        result = await handoff_live.case_tampered_worktree('c2', repo, 30)
        assert result['passed'] and result['state'] == 'needs_attention', result
    asyncio.run(run())


def test_summary_separates_the_clean_control_from_the_defects():
    records = [
        {'case': 'a1/handoff', 'passed': True, 'delivered': True, 'seconds': 10, 'tokens': 5},
        {'case': 'a1/baseline', 'passed': False, 'seconds': 4, 'tokens': 2},
        {'case': 'b1/review', 'defect': True, 'found': True, 'flagged': True},
        {'case': 'b2/review', 'defect': True, 'found': False, 'flagged': False},
        {'case': 'b6/review', 'defect': False, 'found': None, 'flagged': True},
        {'case': 'c1-hostile-document', 'passed': True},
        {'case': 'c2-tampered-worktree', 'exception': 'boom'},
    ]
    summary = handoff_live.summarize(records)
    assert summary['A_handoff_pass_rate'] == 1 and summary['A_baseline_pass_rate'] == 0
    assert summary['B_defects_found_rate'] == .5 and summary['B_false_alarm_rate'] == 1
    assert summary['C_safety_pass_rate'] == .5 and summary['errors'] == 1


def test_every_feature_has_a_runnable_hidden_test_and_every_review_a_requirement():
    for case in FEATURES:
        compile(hidden_test(case), case, 'exec')
    assert sum(1 for *_, expect in REVIEWS.values() if expect is None) == 1
