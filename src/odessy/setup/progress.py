"""Bounded, redacted installer progress snapshots."""
import json
import re
import threading
import time
from datetime import datetime, timezone


def redact(line):
    from odessy.authority.redaction import redact_for_log
    line = redact_for_log(line)
    line = re.sub(r'(?i)(authorization\s*[:=]\s*|bearer\s+)[^\r\n]+', r'\1[REDACTED]', line)
    line = re.sub(r'(?i)((?:api[_-]?key|password|passwd|secret|token)\s*[=:]\s*)\S+', r'\1[REDACTED]', line)
    line = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', line)
    return re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', line).rstrip('\r\n')[:4096]


class Progress:
    def __init__(self, journal, persist):
        self.journal, self.persist = journal, persist
        self.lock = threading.RLock()
        self.last_write = 0
        journal.update(started_at=time.time(), stage_started_at=time.time(), logs=[], log_cursor=0)

    def flush(self, force=False):
        with self.lock:
            now = time.monotonic()
            if force or now-self.last_write >= .25:
                self.persist()
                self.last_write = now

    def append(self, stream, line):
        with self.lock:
            j = self.journal
            j['log_cursor'] += 1
            j['logs'].append(dict(seq=j['log_cursor'], timestamp=datetime.now(timezone.utc).isoformat(),
                                  stage=j.get('stage'), stream=stream, line=redact(line)))
            while len(j['logs']) > 2000 or len(json.dumps(j['logs'],ensure_ascii=False).encode('utf-8')) > 256*1024:
                j['logs'].pop(0)
            self.flush()

    def stage(self, name):
        with self.lock:
            self.journal.update(stage=name, stage_started_at=time.time(), steps_done=0)
            self.append('stage', name)
            self.flush(force=True)


def snapshot(journal, cursor=0):
    j = journal or {}
    end = j.get('finished_at',time.time())
    logs = j.get('logs', [])
    return dict(elapsed_ms=max(0,int((end-j['started_at'])*1000)) if 'started_at' in j else None,
                stage_elapsed_ms=max(0,int((end-j['stage_started_at'])*1000)) if 'stage_started_at' in j else None,
                logs=[item for item in logs if item['seq']>cursor], log_cursor=j.get('log_cursor',0),
                logs_truncated=bool(logs and cursor<logs[0]['seq']-1))
