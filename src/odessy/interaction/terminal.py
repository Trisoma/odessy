"""Cancellation-safe foreground terminal input, with no blocked input thread."""
from __future__ import annotations

import asyncio
import os
import sys
import json
import contextlib
from dataclasses import dataclass
from typing import TextIO


class TerminalUnavailable(RuntimeError):
    pass


class TerminalEOF(TerminalUnavailable):
    """The interactive terminal was closed, rather than an empty line entered."""


class TerminalInterrupted(TerminalUnavailable):
    """Ctrl+C from the Windows console reader."""


def discard_terminal_input(stream: TextIO) -> None:
    """Discard pre-cue keyboard bytes when a foreground prompt changes owners."""
    if not stream.isatty():
        return
    with contextlib.suppress(OSError, ValueError, AttributeError, ImportError):
        if os.name == 'nt':
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios
            termios.tcflush(stream.fileno(), termios.TCIFLUSH)


async def read_terminal_line(stream: TextIO, output: TextIO) -> str:
    if not stream.isatty():
        raise TerminalUnavailable('Interactive keyboard input requires a terminal; use the interaction API.')
    if os.name == 'nt':
        import msvcrt
        chars: list[str] = []
        while True:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ('\r', '\n'):
                    output.write('\n'); output.flush()
                    return ''.join(chars)
                if char == '\x03':
                    raise TerminalInterrupted('Keyboard interrupt')
                if char in ('\x04', '\x1a'):
                    raise TerminalEOF('Terminal input ended')
                if char in ('\x00', '\xe0'):
                    msvcrt.getwch()
                elif char == '\b':
                    if chars:
                        chars.pop(); output.write('\b \b'); output.flush()
                else:
                    chars.append(char); output.write(char); output.flush()
            await asyncio.sleep(.03)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    fd = stream.fileno()
    chars = bytearray()
    def ready():
        if not future.done():
            try:
                # Avoid TextIOWrapper read-ahead: a second pretyped line must not
                # survive an ownership change and answer a later approval prompt.
                char = os.read(fd, 1)
                if not char:
                    future.set_exception(TerminalEOF('Terminal input ended'))
                elif char == b'\n':
                    future.set_result(chars.decode(stream.encoding or 'utf-8', 'replace'))
                else:
                    chars.extend(char)
            except Exception as error:
                future.set_exception(error)
    loop.add_reader(fd, ready)
    try:
        return await future
    finally:
        loop.remove_reader(fd)


def format_interaction(entry: dict) -> str:
    lines = [entry.get('question') or entry.get('prompt', '')]
    if entry['kind'] == 'approval':
        lines.extend(name + ': ' + json.dumps(entry.get(name), sort_keys=True, ensure_ascii=False)
                     for name in ('operation', 'host', 'arguments', 'scopes', 'allowed_channels'))
    if entry.get('choices'):
        lines.append('Choices: ' + ', '.join(map(str, entry['choices'])))
    if entry['kind'] == 'approval':
        lines.append('Type approve this action to approve, or deny to reject.')
    return '\n'.join(lines)


def answer_interaction(broker, entry: dict, text: str, *, stop_turn=None) -> str:
    """Validate a reply for this exact request; never interpret it as a new turn."""
    from odessy.interaction.broker import InteractionError
    text = text.strip()
    answer = text.casefold()
    if not broker.is_pending(entry['id']):
        return 'stale'
    if answer in ('stop', 'cancel'):
        broker.cancel_pending('stopped from terminal')
        from odessy.voice.interaction import schedule_stop
        schedule_stop(stop_turn)
        return 'stopped'
    try:
        if entry['kind'] == 'question':
            if text and (entry.get('allow_free_text', True) or text in entry.get('choices', ())):
                broker.resolve_question(entry['id'], text, channel='terminal')
                return 'resolved'
        elif answer in ('approve this action', 'deny', 'no', 'reject'):
            broker.resolve_approval(entry['id'], answer == 'approve this action', channel='terminal')
            return 'resolved'
    except InteractionError as error:
        return 'stale' if error.status_code in (404, 409, 410) else 'invalid'
    return 'invalid'


@dataclass
class TerminalInteractionHandler:
    broker: object
    stdin: TextIO | None = None
    stdout: TextIO | None = None
    stop_turn: object = None
    mark_ready: bool = True

    async def __call__(self, entry: dict) -> None:
        stream, output = self.stdin or sys.stdin, self.stdout or sys.stdout
        if not stream.isatty():
            raise TerminalUnavailable('Interactive keyboard input requires a terminal; use the interaction API.')
        output.write(format_interaction(entry) + '\n')
        output.flush()
        if self.mark_ready:
            self.broker.mark_ready(entry['id'])
        while self.broker.is_pending(entry['id']):
            try:
                text = (await read_terminal_line(stream, output)).strip()
            except TerminalInterrupted:
                text = 'cancel'
            if not text:
                raise TerminalUnavailable('Terminal input ended; use the interaction API.')
            if answer_interaction(self.broker, entry, text, stop_turn=self.stop_turn) != 'invalid':
                return
            output.write('Response not accepted; please answer the pending prompt.\n'); output.flush()


def race_handlers(*handlers):
    """Keep available transports alive; broker cancellation drains all readers."""
    async def handle(entry):
        tasks = {asyncio.create_task(handler(entry)) for handler in handlers}
        all_tasks = set(tasks)
        try:
            while tasks:
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        task.result()
                    except TerminalUnavailable:
                        continue
                    else:
                        return
        finally:
            for task in all_tasks:
                task.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)
    return handle
