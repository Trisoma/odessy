"""Threaded wizard adapter; installation decisions remain in the workflow module."""
from __future__ import annotations
import contextlib
import io
import queue
import threading
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

from installer import workflow
from installer.checkpoint import fingerprint, read_state, state_path


@dataclass(frozen=True)
class Options:
    install_dir: str
    cache_dir: str
    tool_bundle: str
    endpoint: str = 'http://127.0.0.1:11435'
    timeout: str = '180'
    resume: bool = False


def comparable(plan):
    return {key: value for key, value in plan.items() if key != 'timeout'}


class Backend:
    def preview(self, options: Options):
        if not all(value.strip() for value in (options.install_dir, options.cache_dir, options.tool_bundle)):
            raise ValueError('Select the installation directory, cache directory and installation resource directory.')
        try:
            timeout = int(options.timeout)
        except ValueError as error:
            raise ValueError('The check timeout must be a whole number of seconds between 15 and 600.') from error
        root = Path(options.install_dir.strip())
        plan = workflow.build_plan(root, Path(options.cache_dir.strip()), Path(options.tool_bundle.strip()),
                                   options.endpoint.strip(), timeout)
        root = Path(plan['install_dir'])
        if state_path(root).exists():
            previous = read_state(root)
            if not options.resume:
                raise ValueError('This directory already has an installation record. Load the record first, or choose another empty directory for a new installation.')
            if comparable(previous['plan']) != comparable(plan):
                raise ValueError('The installation record does not match this installer version, path or resources. Resume with the original version or choose a new directory; the existing installation is preserved.')
        elif options.resume:
            raise ValueError('There is no installation record to resume in this directory.')
        elif root.exists() and any(root.iterdir()):
            raise ValueError('The installation directory is not empty. Choose a new or empty folder; existing files will not be overwritten.')
        volumes = workflow.combined_budget(plan)
        return dict(plan=plan, volumes=volumes, sufficient=all(v['sufficient'] for v in volumes),
                    resume=options.resume, options=asdict(options), options_sha256=fingerprint(asdict(options)))

    def record(self, options: Options):
        root = Path(options.install_dir.strip())
        workflow.model.guard_path(root)
        from installer.uninstall import removal_started
        if removal_started(root): raise ValueError('This installation has been uninstalled. Use "Uninstall / clean this directory…" to review or continue, or choose a new directory for a new installation.')
        state = read_state(root)
        plan = state['plan']
        restored = Options(str(root), plan['cache_dir'], plan['tool_bundle'], plan['endpoint'], str(plan['timeout']), True)
        return {'state': state, 'options': asdict(restored)}

    def shortcuts(self, payload):
        from installer.shortcuts import create
        return create(Path(payload['install_dir']),Path(payload['executable']),
                      desktop=payload['desktop'],start_menu=payload['start_menu'])

    def execute(self, preview, cancelled, emit):
        return workflow.execute(preview['plan'], resume=preview['resume'], emit=emit, cancelled=cancelled)


class QueueLog(io.TextIOBase):
    def __init__(self, messages):
        self.messages = messages; self.pending = ''

    def write(self, value):
        value = str(value); self.pending += value
        while '\n' in self.pending or len(self.pending) > 4000:
            if '\n' in self.pending and self.pending.index('\n') <= 4000:
                line, self.pending = self.pending.split('\n', 1)
            else:
                line, self.pending = self.pending[:4000], self.pending[4000:]
            try:
                self.messages.put_nowait({'kind': 'log', 'text': line})
            except queue.Full:
                pass  # Diagnostics may be dropped; stage/result events never are.
        return len(value)

    def flush(self):
        if self.pending:
            try:
                self.messages.put_nowait({'kind': 'log', 'text': self.pending})
            except queue.Full:
                pass
            self.pending = ''


def friendly_error(error: str) -> str:
    if 'exceeded' in error or 'timed out' in error:
        return 'The check timed out and installation did not complete. Increase the timeout under Advanced settings and resume; see the diagnostic log for details.'
    if 'Another installation workflow' in error:
        return 'Another installation workflow is using this directory. Wait for it to finish; do not install concurrently.'
    if 'Insufficient' in error or 'Not enough space' in error or 'budget failed' in error:
        return 'Insufficient disk space. Check the installation and cache directories.'
    if 'Incomplete or invalid component' in error or 'corrupt' in error.lower():
        return 'Existing components are incomplete or failed validation; files were preserved. See the diagnostic log, then choose a new installation directory or inspect the original files.'
    return error[:1200] or 'The operation did not complete; see the diagnostic log.'


class Controller:
    def __init__(self, backend=None):
        self.backend = backend or Backend()
        self.messages = queue.Queue(maxsize=2048)
        self.cancel = threading.Event()
        self.thread = None
        self.busy = False
        self.job = None

    def start(self, job, payload):
        if self.busy or (self.thread is not None and self.thread.is_alive()):
            raise ValueError('A task is still running.')
        if job not in {'preview', 'record', 'execute', 'shortcuts'}:
            raise ValueError('Unknown wizard job.')
        self.cancel.clear(); self.busy = True; self.job = job
        self.thread = threading.Thread(target=self._work, args=(job, payload), name='odessy-installer', daemon=False)
        self.thread.start()

    def request_cancel(self):
        if self.busy:
            self.cancel.set(); return True
        return False

    def _work(self, job, payload):
        log = QueueLog(self.messages)
        outcome = None
        try:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    if job == 'preview':
                        result = self.backend.preview(payload)
                    elif job == 'shortcuts':
                        result = self.backend.shortcuts(payload)
                    elif job == 'record':
                        result = self.backend.record(payload)
                    else:
                        result = self.backend.execute(payload, self.cancel.is_set,
                            lambda event: self.messages.put({'kind': 'progress', 'event': dict(event)}))
                    if self.cancel.is_set() and job not in {'execute','shortcuts'}:
                        raise KeyboardInterrupt()
                    outcome = {'kind': 'finished', 'job': job, 'result': result}
                except KeyboardInterrupt:
                    outcome = {'kind': 'cancelled', 'job': job}
                except Exception as error:
                    log.write(traceback.format_exc())
                    outcome = {'kind': 'error', 'job': job, 'error': str(error), 'message': friendly_error(str(error))}
                finally:
                    log.flush()
        finally:
            # No Tk calls from this thread. Standard streams are restored first.
            self.messages.put(outcome or {'kind': 'error', 'job': job, 'message': 'The installer worker ended unexpectedly.', 'error': 'worker exited'})
            self.busy = False
