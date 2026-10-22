"""Local voice intent handling. Authorization stays with the interaction broker."""
from __future__ import annotations

import re
import asyncio
import inspect
import json

_stop_tasks = set()


def schedule_stop(callback) -> None:
    if callback is None:
        return
    result = callback()
    if inspect.isawaitable(result):
        task = asyncio.ensure_future(result)
        _stop_tasks.add(task)
        def finished(done):
            _stop_tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)


def interaction_details(entry: dict) -> str:
    return '\n'.join(name + ': ' + json.dumps(entry.get(name), sort_keys=True, ensure_ascii=False)
                     for name in ('operation', 'host', 'arguments', 'scopes', 'allowed_channels'))


def normalize_answer(text: str) -> str:
    return ' '.join(re.findall(r"[\w']+", text.casefold()))


def voice_allowed(entry: dict) -> bool:
    return ('voice' in entry.get('allowed_channels', ())
            and (entry['kind'] == 'question' or entry.get('risk') == 'medium')
            and not entry.get('sandbox_expansion', False))


def interaction_prompt(entry: dict) -> str:
    text = _interaction_prompt(entry)
    return re.sub(r'approve\s+this\s+action', 'the displayed approval phrase', text, flags=re.IGNORECASE)


def _interaction_prompt(entry: dict) -> str:
    if entry['kind'] == 'question':
        text = 'I need one detail before I continue. ' + entry.get('question', entry.get('prompt', ''))
        if entry.get('choices'):
            text += ' Choices: ' + '; '.join(str(x) for x in entry['choices']) + '.'
        return text
    text = 'Odessy requests permission. ' + entry.get('prompt', '')
    if not voice_allowed(entry):
        return text + ' Please respond in the terminal or authenticated interface. Voice cannot authorize this action.'
    # Never play the positive phrase into the microphone. It is displayed as text.
    return text + ' Say the exact approval phrase shown on screen after the cue, or say deny.'


def resolve_spoken_answer(entry: dict, broker, text: str) -> bool:
    """Resolve only valid local intent; never forward an answer to the model."""
    if not broker.is_pending(entry['id']) or not voice_allowed(entry):
        return False
    answer = normalize_answer(text)
    if entry['kind'] == 'approval':
        if answer == 'approve this action':
            broker.resolve_approval(entry['id'], True, channel='voice')
            return True
        if answer in ('deny', 'no', 'reject'):
            broker.resolve_approval(entry['id'], False, channel='voice')
            return True
        return False
    if not text.strip():
        return False
    if not entry.get('allow_free_text', True) and text.strip() not in entry.get('choices', ()):
        return False
    broker.resolve_question(entry['id'], text.strip(), channel='voice')
    return True
