"""Cancellation-safe foreground terminal input, with no blocked input thread."""
from __future__ import annotations

import asyncio
import os
import sys
import json
from dataclasses import dataclass
from typing import TextIO


class TerminalUnavailable(RuntimeError):
    pass


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
                    return 'cancel'
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
    def ready():
        if not future.done():
            try:
                future.set_result(stream.readline())
            except Exception as error:
                future.set_exception(error)
    loop.add_reader(fd, ready)
    try:
        return await future
    finally:
        loop.remove_reader(fd)


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
        prompt = entry.get('question') or entry.get('prompt', '')
        output.write(prompt + '\n')
        if entry['kind'] == 'approval':
            for name in ('operation', 'host', 'arguments', 'scopes', 'allowed_channels'):
                output.write(name + ': ' + json.dumps(entry.get(name), sort_keys=True, ensure_ascii=False) + '\n')
        if entry.get('choices'):
            output.write('Choices: ' + ', '.join(map(str, entry['choices'])) + '\n')
        if entry['kind'] == 'approval':
            output.write('Type approve this action to approve, or deny to reject.\n')
        output.flush()
        if self.mark_ready:
            self.broker.mark_ready(entry['id'])
        while self.broker.is_pending(entry['id']):
            text = (await read_terminal_line(stream, output)).strip()
            if not text:
                raise TerminalUnavailable('Terminal input ended; use the interaction API.')
            answer = text.casefold()
            if answer in ('stop', 'cancel'):
                self.broker.cancel_pending('stopped from terminal')
                from odessy.voice.interaction import schedule_stop
                schedule_stop(self.stop_turn)
                return
            if entry['kind'] == 'question':
                if entry.get('allow_free_text', True) or text in entry.get('choices', ()):
                    self.broker.resolve_question(entry['id'], text, channel='terminal')
                    return
            elif answer in ('approve this action', 'deny', 'no', 'reject'):
                self.broker.resolve_approval(entry['id'], answer == 'approve this action', channel='terminal')
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
