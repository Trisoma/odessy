"""Private local history of provider-reported usage; never stores prompt content."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import threading


def default_history_path():
    explicit = os.environ.get('ODESSY_CONFIG_FILE')
    if explicit:
        return Path(explicit).expanduser().parent / 'usage-history.sqlite3'
    return Path.home() / '.config' / 'odessy' / 'usage-history.sqlite3'


class UsageHistory:
    """SQLite records for completed cloud calls with provider-reported usage."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ready = False
        self.recording_error = False

    def _connect(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.path.exists():
            self._ready = False
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        if os.name != 'nt':
            os.chmod(self.path, 0o600)
        if not self._ready:
            with self._lock:
                if not self._ready:
                    version = db.execute('PRAGMA user_version').fetchone()[0]
                    if version not in (0, 1):
                        db.close()
                        raise RuntimeError('Unsupported usage database version')
                    db.execute('''CREATE TABLE IF NOT EXISTS usage_requests (
                        id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL,
                        provider TEXT, model TEXT, participant TEXT,
                        prompt INTEGER, cached INTEGER, completion INTEGER,
                        cost REAL, currency TEXT)''')
                    db.execute('CREATE INDEX IF NOT EXISTS usage_time ON usage_requests(recorded_at DESC, id DESC)')
                    db.execute('PRAGMA user_version = 1')
                    db.commit()
                    self._ready = True
        return db

    def record(self, *, provider, model, participant, prompt, cached, completion,
               cost, currency, timestamp=None):
        timestamp = timestamp or datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        values = (timestamp, provider, model, participant, prompt, cached, completion, cost, currency)
        with closing(self._connect()) as db, db:
            db.execute('''INSERT INTO usage_requests
                (recorded_at,provider,model,participant,prompt,cached,completion,cost,currency)
                VALUES (?,?,?,?,?,?,?,?,?)''', values)

    def query(self, *, days=30, provider=None, model=None, limit=50, offset=0):
        if days not in (0, 7, 30, 90):
            raise ValueError('Unsupported range')
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise ValueError('Invalid pagination')
        conditions, args = [], []
        if days:
            today = datetime.now(timezone.utc).date()
            since = (today - timedelta(days=days - 1)).isoformat() + 'T00:00:00Z'
            until = (today + timedelta(days=1)).isoformat() + 'T00:00:00Z'
            conditions.extend(('recorded_at >= ?', 'recorded_at < ?'))
            args.extend((since, until))
        for column, value in (('provider', provider), ('model', model)):
            if value is not None:
                if not isinstance(value, str) or not 1 <= len(value) <= 200:
                    raise ValueError('Invalid filter')
                conditions.append(f'{column} = ?'); args.append(value)
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        aggregate = '''COUNT(*) AS requests,
            CASE WHEN COUNT(*)=COUNT(prompt) THEN SUM(prompt) END AS prompt,
            CASE WHEN COUNT(*)=COUNT(cached) THEN SUM(cached) END AS cached,
            CASE WHEN COUNT(*)=COUNT(completion) THEN SUM(completion) END AS completion,
            CASE WHEN COUNT(*)=COUNT(cost) AND COUNT(DISTINCT currency)=1
                 AND COUNT(*)=COUNT(currency) THEN SUM(cost) END AS cost,
            CASE WHEN COUNT(*)=COUNT(cost) AND COUNT(DISTINCT currency)=1
                 AND COUNT(*)=COUNT(currency) THEN MIN(currency) END AS currency'''
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            summary = dict(db.execute('SELECT ' + aggregate + ' FROM usage_requests' + where, args).fetchone())
            daily = [dict(row) for row in db.execute('SELECT substr(recorded_at,1,10) AS day, ' + aggregate +
                     ' FROM usage_requests' + where + ' GROUP BY day ORDER BY day', args)]
            groups = [dict(row) for row in db.execute('SELECT provider, model, ' + aggregate +
                      ' FROM usage_requests' + where + ' GROUP BY provider, model ORDER BY requests DESC LIMIT 100', args)]
            providers = [dict(row) for row in db.execute('SELECT provider, ' + aggregate +
                         ' FROM usage_requests' + where + ' GROUP BY provider ORDER BY requests DESC LIMIT 100', args)]
            requests = [dict(row) for row in db.execute('SELECT id,recorded_at,provider,model,participant,prompt,cached,completion,cost,currency '
                        'FROM usage_requests' + where + ' ORDER BY recorded_at DESC,id DESC LIMIT ? OFFSET ?',
                        [*args, limit, offset])]
            options = [dict(row) for row in db.execute('SELECT DISTINCT provider,model FROM usage_requests ORDER BY provider,model LIMIT 500')]
        return {'schema_version': 1, 'summary': summary, 'daily': daily, 'groups': groups, 'providers': providers,
                'requests': requests, 'total': summary['requests'], 'limit': limit, 'offset': offset,
                'options': options, 'recording_error': self.recording_error}
