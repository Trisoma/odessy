import sys
from odessy.setup.progress import Progress, snapshot
from odessy.setup.maintenance import run, Cancelled
import pytest


def test_bounded_redacted_cursor():
    journal = {}
    writes = []
    progress = Progress(journal, lambda: writes.append(1))
    progress.stage('dependencies')
    for _ in range(2100):
        progress.append('stderr', 'api_key=secret-value ' + 'x'*200)
    result = snapshot(journal)
    assert result['logs_truncated']
    assert len(result['logs']) <= 2000
    assert 'secret-value' not in str(result)
    assert snapshot(journal,result['log_cursor'])['logs'] == []
    assert result['elapsed_ms'] >= 0


def test_run_drains_both_streams_and_failure():
    lines = []
    with pytest.raises(RuntimeError, match='child failed'):
        run([sys.executable, '-c', 'import sys; print("out"); print("err",file=sys.stderr); sys.exit(2)'],
            output=lambda stream,line: lines.append((stream,line.strip())))
    assert ('stdout','out') in lines
    assert ('stderr','err') in lines


def test_run_cancellation():
    with pytest.raises(Cancelled):
        run([sys.executable,'-c','import time; time.sleep(30)'], cancelled=lambda:True)


def test_progress_reuses_secret_redaction_for_json_fields():
    journal={}
    progress=Progress(journal,lambda:None)
    progress.append('stdout','{"password":123456789,"OPENAI_API_KEY":"sensitive-value"}')
    assert '123456789' not in str(journal)
    assert 'sensitive-value' not in str(journal)


def test_overlong_log_line_is_discarded_before_redaction_chunks():
    journal={}
    progress=Progress(journal,lambda:None)
    run([sys.executable,'-c',
         'print("password="+"x"*4100+"CONTINUATION_SECRET"); print("safe output")'],
        output=progress.append)
    assert 'CONTINUATION_SECRET' not in str(journal)
    assert 'safe output' in str(journal)
