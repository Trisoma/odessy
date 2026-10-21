"""Background coding CLI jobs. Child CLIs retain their own permission systems.

Configured commands/adapters are trusted operator settings, never model arguments.
CLI subscriptions and provider-reported CLI usage are outside the API UsageLedger.
Worktrees are deliberately retained for inspection, including after cancellation.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import uuid
from dataclasses import asdict, is_dataclass


TERMINAL = {'completed', 'failed', 'cancelled', 'needs_attention'}
_repository_reservations = {}
_repository_lock = threading.RLock()


def _cli_usage(output='', reported=None):
    """Validate separately reported CLI figures; never debit the API ledger."""
    usage = dict(source='coding_cli', outside_api_budget=True, input_tokens=None,
                 cached_input_tokens=None, output_tokens=None, cost=None, currency=None)

    def number(value, limit, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value < 0 or value > limit or not math.isfinite(value):
            return None
        if integer and int(value) != value:
            return None
        return int(value) if integer else float(value)

    reports = []
    for line in str(output).splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get('type') not in ('turn.completed', 'result'):
            continue
        if isinstance(event.get('usage'), dict):
            report = dict(event['usage'])
            if 'total_cost_usd' in event:
                report['total_cost_usd'] = event['total_cost_usd']
            reports.append(report)
    if isinstance(reported, dict):
        reports.append(reported)
    for report in reports:
        token = lambda key: number(report.get(key), 2**53 - 1, integer=True)
        incoming = token('input_tokens')
        cached = token('cached_input_tokens')
        if 'cache_read_input_tokens' in report or 'cache_creation_input_tokens' in report:
            cached = token('cache_read_input_tokens') if 'cache_read_input_tokens' in report else 0
            created = token('cache_creation_input_tokens') if 'cache_creation_input_tokens' in report else 0
            incoming = (number(incoming + cached + created, 2**53 - 1, integer=True)
                        if incoming is not None and cached is not None and created is not None else None)
        for key, value in (('input_tokens', incoming), ('cached_input_tokens', cached),
                           ('output_tokens', token('output_tokens'))):
            if value is not None:
                usage[key] = value
        usd_cost = number(report.get('total_cost_usd'), 1e12)
        cost = usd_cost if usd_cost is not None else number(report.get('cost'), 1e12)
        if cost is not None:
            usage['cost'] = cost
            currency = 'USD' if usd_cost is not None else report.get('currency')
            usage['currency'] = currency if isinstance(currency, str) and 0 < len(currency) <= 16 and currency.isalpha() else None
    if usage['input_tokens'] is not None and usage['cached_input_tokens'] is not None:
        usage['cached_input_tokens'] = min(usage['cached_input_tokens'], usage['input_tokens'])
    return usage


def _result_text(output):
    """Extract documented Claude result and Codex agent-message JSON output."""
    messages = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'result' and isinstance(event.get('result'), str):
            messages.append(event['result'])
        item = event.get('item', {})
        if isinstance(item, dict) and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            messages.append(item['text'])
    return '\n'.join(messages) if messages else output


def consult_agent(profile, question, context=''):
    """Synchronous service adapter; call from a worker, never an event-loop thread.

    Consultation uses the configured advice mode and the same CLI restrictions,
    redaction, timeout cancellation, and unknown/outside-API accounting as jobs.
    """
    if 'consult' not in getattr(profile, 'capabilities', ()):
        raise ValueError('participant does not allow consult capability')
    async def consult():
        manager = JobManager({profile.name: profile})
        try:
            prompt = question + ('\n\nContext: ' + context if context else '')
            job = await manager._submit_consult(profile.name, prompt)
            try:
                await asyncio.wait_for(asyncio.shield(manager._tasks[job['job_id']]),
                                       timeout=getattr(profile, 'timeout', 120))
            except asyncio.TimeoutError:
                await manager.cancel(job['job_id'])
                raise TimeoutError('coding CLI consultation timed out') from None
            result = manager.get(job['job_id'])
            if result['state'] != 'completed':
                raise RuntimeError(result['error'] or result['result'] or 'coding CLI consultation failed')
            return _result_text(result['result'])
        finally:
            await manager.close()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(consult())
    raise RuntimeError('consult_agent must run in a worker thread')


class JobManager:
    def __init__(self, profiles, event_sink=None, remote_targets=None, runner=None,
                 adapters=None, max_log_chars=32000, max_progress=100):
        self.profiles = profiles
        self.event_sink = event_sink
        self.remote_targets = remote_targets or {}
        self.runner = runner or self._subprocess
        self.adapters = adapters or {}
        self.max_log_chars = max_log_chars
        self.max_progress = max_progress
        self._jobs, self._tasks, self._active, self._secrets = {}, {}, {}, {}
        self._remote_ids = {}
        self._remote_inflight = set()
        self._raw_logs = {}
        self._closed = False

    def get(self, job_id):
        return copy.deepcopy(self._jobs[job_id])

    def _reserved(self, key):
        with _repository_lock:
            return key in _repository_reservations

    def _reserve(self, key, job_id):
        with _repository_lock:
            if key in _repository_reservations:
                raise ValueError('workspace repository has an active job')
            _repository_reservations[key] = job_id
            self._active[key] = job_id

    async def consult(self, profile, question, context=''):
        """Advice on this manager's loop, including explicitly admitted hosts."""
        name = profile if isinstance(profile, str) else profile.name
        configured = self.profiles.get(name)
        if configured is None or 'consult' not in getattr(configured, 'capabilities', ()):
            raise ValueError('participant does not allow consult capability')
        if 'advice' not in getattr(configured, 'allowed_modes', ()):
            raise ValueError('participant does not allow advice consultations')
        if self._closed:
            raise ValueError('job manager is closed')
        host = getattr(configured, 'host', None)
        if host and host != 'local':
            target = self.remote_targets.get(host)
            if target is None:
                raise ValueError('configured execution target is unavailable')
            result = await asyncio.wait_for(self._remote_call(target, 'consult', {
                'participant': name, 'question': question, 'context': context,
            }), getattr(configured, 'timeout', 120))
            if result.get('status') != 'ok' or not isinstance(result.get('answer'), str):
                raise RuntimeError('remote advice host did not return a successful text answer')
            credential = getattr(configured, 'credential_env', None)
            answer = result['answer']
            secret = os.environ.get(credential, '') if credential else ''
            if secret:
                answer = answer.replace(secret, '[REDACTED]')
            return answer[:self.max_log_chars]
        prompt = question + ('\n\nContext: ' + context if context else '')
        job = await self._submit_consult(name, prompt)
        try:
            await asyncio.shield(self._tasks[job['job_id']])
        except asyncio.CancelledError:
            await self.cancel(job['job_id'])
            raise
        result = self.get(job['job_id'])
        if result['state'] != 'completed':
            raise RuntimeError(result['error'] or result['result'] or 'coding CLI consultation failed')
        return _result_text(result['result'])

    def _redact(self, job_id, value):
        text = str(value)
        for secret in self._secrets.get(job_id, ()):
            if secret:
                text = text.replace(secret, '[REDACTED]')
                # A read can end in the middle of a credential. Keep the raw
                # tail private so a later read can finish matching it.
                for size in range(min(len(secret) - 1, len(text)), 0, -1):
                    if text.endswith(secret[:size]):
                        text = text[:-size] + '[REDACTED]'
                        break
        return re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token|password)\s*[=:]\s*)[^\s,\}"\']+', r'\1[REDACTED]', text)

    def _emit(self, job, message=None):
        if message is not None:
            job_id = job['job_id']
            overlap = max((len(s) for s in self._secrets.get(job_id, ())), default=0)
            raw = (self._raw_logs.get(job_id, '') + str(message))[-(self.max_log_chars + overlap):]
            self._raw_logs[job_id] = raw
            job['log'] = self._redact(job_id, raw)[-self.max_log_chars:]
            job['progress'] = [line[-2000:] for line in job['log'].splitlines()[-self.max_progress:]]
        if self.event_sink:
            result = self.event_sink({'type': 'collaboration.job', **self.get(job['job_id'])})
            if inspect.isawaitable(result):
                # Event storage should normally be synchronous; avoid retaining tasks.
                task = asyncio.create_task(result)
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _git(self, cwd, *args, check=True):
        proc = await asyncio.create_subprocess_exec('git', '-C', str(cwd), *args,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        if check and proc.returncode:
            raise ValueError(err.decode(errors='replace').strip() or 'git failed')
        return out.decode(errors='replace').strip()

    async def submit(self, participant, task, mode='advice', workspace=None, approved=False):
        return await self._submit(participant, task, mode, workspace, approved)

    async def _submit_consult(self, participant, task):
        return await self._submit(participant, task, 'advice', None, False, consultation=True)

    def preflight_submission(self, participant, task, mode='advice', workspace=None):
        """Validate configured delegation feasibility without starting any work."""
        return self._preflight_submission(participant, task, mode, workspace, 'delegate')

    def _preflight_submission(self, participant, task, mode, workspace, capability):
        if self._closed:
            raise ValueError('job manager is closed')
        profile = self.profiles.get(participant)
        if profile is None:
            raise ValueError('unknown participant')
        if capability not in getattr(profile, 'capabilities', ()):
            raise ValueError(f'participant does not allow {capability} capability')
        if mode not in getattr(profile, 'allowed_modes', ('advice',)):
            raise ValueError('mode is not allowed by participant configuration')
        if not isinstance(task, str) or not task.strip():
            raise ValueError('task must be nonempty text')
        host = getattr(profile, 'host', None) or 'local'
        configured_workspace = getattr(profile, 'workspace', '') or ''
        remote_identity = None
        if host != 'local':
            target = self.remote_targets.get(host)
            if target is None:
                raise ValueError('configured execution target is unavailable')
            cwd = str(workspace) if workspace is not None else configured_workspace
            remote_identity = str(id(target))
        else:
            root = Path(configured_workspace or Path.cwd()).resolve()
            cwd = Path(workspace or root).resolve()
            if not cwd.is_relative_to(root) or not cwd.is_dir():
                raise ValueError('workspace must be an existing directory under the configured workspace')
            configured_workspace = str(root)
        profile_data = asdict(profile) if is_dataclass(profile) else copy.deepcopy(vars(profile))
        custom = self.adapters.get(profile.adapter)
        return {'host': host, 'participant': participant, 'profile': profile_data,
                'workspace': str(cwd), 'configured_workspace': configured_workspace,
                'remote_identity': remote_identity,
                'adapter_identity': str(id(custom)) if custom is not None else None}

    async def _submit(self, participant, task, mode, workspace, approved, consultation=False):
        identity = self._preflight_submission(participant, task, mode, workspace,
            'consult' if consultation else 'delegate')
        profile = self.profiles[participant]
        if mode != 'advice' and approved is not True:
            raise ValueError('explicit controller approval is required')
        host = getattr(profile, 'host', None)
        if host and host != 'local' and host not in self.remote_targets:
            raise ValueError('configured execution target is unavailable')
        remote = host and host != 'local'
        if remote:
            # Remote paths belong to the target OS. Its configured profile and
            # canonical workspace checks remain authoritative for admission.
            configured_workspace = getattr(profile, 'workspace', '') or ''
            cwd = str(workspace) if workspace is not None else configured_workspace
            repo = None
            key = (host, configured_workspace or str(cwd) or participant)
        else:
            cwd = Path(identity['workspace'])
            repo = Path(await self._git(cwd, 'rev-parse', '--show-toplevel', check=False) or cwd).resolve()
            common = await self._git(cwd, 'rev-parse', '--git-common-dir', check=False)
            identity = (cwd / common).resolve() if common else repo
            key = ('local', str(identity).casefold() if os.name == 'nt' else str(identity))
        if self._reserved(key):
            raise ValueError('workspace repository has an active job')
        if mode == 'patch' and not remote:
            if not await self._git(cwd, 'rev-parse', '--show-toplevel', check=False):
                raise ValueError('patch mode requires a clean git repository')
            if await self._git(repo, 'status', '--porcelain'):
                raise ValueError('patch mode requires a clean git repository')
        job_id = uuid.uuid4().hex
        job = dict(job_id=job_id, participant=participant, mode=mode, task=task,
                   state='queued', workspace=str(cwd), worktree=None, diff='', changed_files=[],
                   validation={'status': 'unknown', 'report': None}, result=None, error=None,
                   progress=[], log='', usage=_cli_usage())
        job['remote_state_unknown'] = False
        job['permissions'] = {
            'source': 'remote_coding_cli' if remote else 'coding_cli', 'adapter': profile.adapter,
            'mode': mode, 'enforcement': 'child_cli_not_verified',
            'requested_sandbox': ('read-only' if mode == 'advice' else 'workspace-write')
                                 if profile.adapter == 'codex' and mode in ('advice', 'patch') else None,
            'requested_tools': ('Read,Glob,Grep' if mode == 'advice' else 'Read,Glob,Grep,Edit,Write')
                               if profile.adapter in ('claude', 'claudecode', 'claude_code') and mode in ('advice', 'patch') else None,
        }
        self._jobs[job_id] = job
        credential = getattr(profile, 'credential_env', None)
        self._secrets[job_id] = [os.environ.get(credential, '')] if credential else []
        try:
            self._reserve(key, job_id)
        except ValueError:
            del self._jobs[job_id]
            del self._secrets[job_id]
            raise
        self._tasks[job_id] = asyncio.create_task(self._execute(job, profile, cwd, repo, key, remote))
        self._emit(job)
        return self.get(job_id)

    def _command(self, profile, task, mode, cwd):
        adapter = profile.adapter
        if adapter in self.adapters:
            return tuple(self.adapters[adapter](profile, task=task, mode=mode, workspace=cwd))
        command = tuple(getattr(profile, 'command', ()) or ())
        model = getattr(profile, 'model', None)
        if adapter == 'codex':
            if mode not in ('advice', 'patch'):
                raise ValueError('custom modes require a registered adapter')
            return (*(command or ('codex',)), 'exec', '--json', '--sandbox',
                    'read-only' if mode == 'advice' else 'workspace-write',
                    *(['--model', model] if model else []), task)
        if adapter in ('claude_code', 'claudecode', 'claude'):
            if mode not in ('advice', 'patch'):
                raise ValueError('custom modes require a registered adapter')
            # --tools restricts the available tools, rather than merely approving them.
            tools = 'Read,Glob,Grep' if mode == 'advice' else 'Read,Glob,Grep,Edit,Write'
            return (*(command or ('claude',)), '-p', task, '--bare', '--output-format', 'json',
                    '--permission-mode', 'dontAsk', '--tools', tools, '--allowedTools', tools,
                    *(['--model', model] if model else []))
        if adapter == 'custom_agent' and command:
            return tuple(part.replace('{task}', task).replace('{workspace}', str(cwd)).replace('{mode}', mode)
                         for part in command)
        raise ValueError('coding adapter requires a registered adapter or configured command')

    def _environment(self, profile):
        # Authentication stored by each CLI in its home/config directory remains its
        # responsibility. Never forward unrelated API credentials from this process.
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP',
                   'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'LANG', 'LC_ALL'}
        credential = getattr(profile, 'credential_env', None)
        if credential:
            allowed.add(credential)
        return {name: value for name, value in os.environ.items() if name.upper() in allowed or name == credential}

    async def _remote_call(self, target, action, payload):
        result = target(action, payload)
        return await result if inspect.isawaitable(result) else result

    async def _execute(self, job, profile, cwd, repo, key, remote):
        final_state = 'failed'
        try:
            job['state'] = 'running'
            self._emit(job)
            if remote:
                target = self.remote_targets[profile.host]
                payload = {k: job[k] for k in ('job_id', 'participant', 'task', 'mode', 'workspace')}
                self._remote_inflight.add(job['job_id'])
                result = await asyncio.wait_for(self._remote_call(target, 'start', payload),
                                                getattr(profile, 'timeout', 120))
                self._remote_ids[job['job_id']] = result.get('job_id', job['job_id'])
                deadline = asyncio.get_running_loop().time() + getattr(profile, 'timeout', 120)
                while result.get('state', 'completed') in ('queued', 'running'):
                    await asyncio.sleep(.1)
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    result = await asyncio.wait_for(self._remote_call(target, 'status',
                        {'job_id': self._remote_ids[job['job_id']]}), remaining)
                for field in ('diff', 'changed_files', 'worktree'):
                    if field in result:
                        value = result[field]
                        job[field] = ([self._redact(job['job_id'], v) for v in value]
                                      if isinstance(value, list) else self._redact(job['job_id'], value))
                if result.get('state') in {'completed', 'failed', 'cancelled'}:
                    self._remote_inflight.discard(job['job_id'])
                if isinstance(result.get('permissions'), dict):
                    job['permissions']['remote_report'] = self._safe_remote_value(job['job_id'], result['permissions'])
            else:
                if job['mode'] == 'patch':
                    worktree = repo.parent / (repo.name + '-odessy-' + job['job_id'][:12])
                    await self._git(repo, 'worktree', 'add', '--detach', str(worktree), 'HEAD')
                    job['worktree'] = str(worktree)
                    cwd = worktree / cwd.relative_to(repo)
                command = self._command(profile, job['task'], job['mode'], cwd)
                result = await asyncio.wait_for(self.runner(command, cwd=cwd, env=self._environment(profile),
                    on_output=lambda output: self._emit(job, output)), getattr(profile, 'timeout', 120))
            job['result'] = self._redact(job['job_id'], result.get('output') or result.get('result') or '')
            job['usage'] = _cli_usage(result.get('output') or '', result.get('usage'))
            if result.get('error'):
                job['error'] = self._redact(job['job_id'], result['error'])
            final_state = result.get('state', 'completed' if result.get('returncode', 0) == 0 else 'failed')
            if final_state not in TERMINAL:
                final_state = 'needs_attention'
            if result.get('validation'):
                validation = result['validation']
                status = 'unknown' if isinstance(validation, dict) and validation.get('status') == 'unknown' else 'reported'
                job['validation'] = {'status': status, 'report': self._redact(job['job_id'], validation)}
        except asyncio.CancelledError:
            final_state = 'cancelled'
            raise
        except asyncio.TimeoutError:
            final_state, job['error'] = 'needs_attention', 'coding CLI job timed out'
            if remote:
                try:
                    response = await asyncio.wait_for(self._remote_call(self.remote_targets[profile.host], 'cancel',
                        {'job_id': self._remote_ids.get(job['job_id'], job['job_id'])}), 5)
                    if response.get('state') in {'completed', 'failed', 'cancelled'}:
                        self._remote_inflight.discard(job['job_id'])
                except Exception:
                    pass
        except Exception as exc:
            final_state, job['error'] = 'failed', self._redact(job['job_id'], exc)
        finally:
            if job['worktree'] and not remote:
                try:
                    tree = Path(job['worktree'])
                    # Include untracked files in the review diff without committing.
                    await self._git(tree, 'add', '-N', '.')
                    job['diff'] = self._redact(job['job_id'], await self._git(tree, 'diff', '--no-ext-diff', 'HEAD'))
                    job['changed_files'] = (await self._git(tree, 'diff', '--name-only', 'HEAD')).splitlines()
                except Exception as exc:
                    job['error'] = self._redact(job['job_id'], exc)
                    if final_state == 'completed':
                        final_state = 'needs_attention'
            job['remote_state_unknown'] = job['job_id'] in self._remote_inflight
            job['state'] = 'needs_attention' if job['remote_state_unknown'] else final_state
            if not job['remote_state_unknown']:
                self._release(job['job_id'])
            self._emit(job)

    def _safe_remote_value(self, job_id, value):
        if isinstance(value, dict):
            return {str(k): self._safe_remote_value(job_id, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._safe_remote_value(job_id, v) for v in value]
        return self._redact(job_id, value) if isinstance(value, str) else value

    def _release(self, job_id):
        with _repository_lock:
            for key, value in list(self._active.items()):
                if value == job_id:
                    if _repository_reservations.get(key) == job_id:
                        del _repository_reservations[key]
                    del self._active[key]

    async def refresh(self, job_id):
        """Explicitly verify uncertain remote jobs before releasing their workspace."""
        job = self._jobs[job_id]
        if job_id not in self._remote_inflight:
            return self.get(job_id)
        profile = self.profiles[job['participant']]
        try:
            result = await asyncio.wait_for(self._remote_call(self.remote_targets[profile.host], 'status',
                {'job_id': self._remote_ids.get(job_id, job_id)}), min(getattr(profile, 'timeout', 120), 5))
            if result.get('state') in {'completed', 'failed', 'cancelled'}:
                self._remote_inflight.discard(job_id)
                job['remote_state_unknown'] = False
                job['state'] = result['state']
                for field in ('diff', 'changed_files', 'worktree', 'validation', 'result', 'permissions', 'error'):
                    if field in result:
                        job[field] = self._safe_remote_value(job_id, result[field])
                if isinstance(result.get('usage'), dict):
                    job['usage'] = _cli_usage(reported=result['usage'])
                self._release(job_id)
        except Exception as exc:
            job['error'] = self._redact(job_id, f'remote state could not be verified: {exc}')
        self._emit(job)
        return self.get(job_id)

    async def cancel(self, job_id):
        job = self._jobs[job_id]
        if job['state'] in TERMINAL and job_id not in self._remote_inflight:
            return self.get(job_id)
        profile = self.profiles[job['participant']]
        confirmed_state = None
        if job_id in self._remote_inflight:
            try:
                response = await asyncio.wait_for(self._remote_call(self.remote_targets[profile.host], 'cancel',
                    {'job_id': self._remote_ids.get(job_id, job_id)}), 5)
                if response.get('state') in {'completed', 'failed', 'cancelled'}:
                    confirmed_state = response['state']
                    self._remote_inflight.discard(job_id)
            except Exception as exc:
                job['error'] = self._redact(job_id, f'remote cancellation could not be confirmed: {exc}')
        self._tasks[job_id].cancel()
        await asyncio.gather(self._tasks[job_id], return_exceptions=True)
        job['remote_state_unknown'] = job_id in self._remote_inflight
        job['state'] = 'needs_attention' if job['remote_state_unknown'] else (confirmed_state or 'cancelled')
        if not job['remote_state_unknown']:
            self._release(job_id)
        self._emit(job)
        return self.get(job_id)

    async def close(self):
        self._closed = True
        for job_id in list(self._jobs):
            if self._jobs[job_id]['state'] not in TERMINAL or job_id in self._remote_inflight:
                await self.cancel(job_id)

    async def _subprocess(self, command, *, cwd, env, on_output):
        kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        proc = await asyncio.create_subprocess_exec(*command, cwd=str(cwd), env=env,
                 stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                 stderr=asyncio.subprocess.STDOUT, **kwargs)
        output = ''
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                text = chunk.decode(errors='replace')
                output = (output + text)[-self.max_log_chars:]
                on_output(text)
            await proc.wait()
        except asyncio.CancelledError:
            if proc.returncode is None:
                if os.name == 'nt':
                    killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(proc.pid), '/T', '/F',
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await killer.wait()
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await proc.wait()
            raise
        result = {'returncode': proc.returncode, 'output': output}
        try:
            # Claude emits one result object; Codex emits JSONL events.
            events = [json.loads(line) for line in output.splitlines() if line.strip()]
            for event in events:
                if isinstance(event, dict) and event.get('is_error'):
                    result['returncode'] = 1
        except (ValueError, TypeError):
            pass
        return result
