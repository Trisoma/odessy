"""Terminal/voice composition over one existing session, not a second controller."""
from __future__ import annotations

import asyncio
import contextlib
import sys

from odessy.application.bootstrap import ControllerAgent
from odessy.interaction.terminal import (
    TerminalEOF, TerminalInterrupted, TerminalUnavailable, answer_interaction,
    discard_terminal_input, format_interaction, read_terminal_line,
)


class ForegroundBusy(RuntimeError):
    pass


class HybridConsole:
    HELP = 'Type a request. Commands: /text /voice /stop /help /exit'

    def __init__(self, controller, *, audio=None, stdin=None, stdout=None,
                 read_line=None, discard_input=None, persistent=True, turn_context=None):
        self.controller, self.audio = controller, audio
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self.read_line = read_line or read_terminal_line
        self.discard_input = discard_input or discard_terminal_input
        self.persistent = persistent
        self.turn_context = turn_context or contextlib.nullcontext
        self.agent = ControllerAgent(controller, isolate_turn=True)
        self.input_generation = 0
        self._input_changed = asyncio.Event()
        self._closing = asyncio.Event()
        self._entry = None
        self._turn_task = None
        self._turn_stop = None
        self._audio_start = None
        self._resume_requested = False
        self._reserved = False
        self._voice_enabled = True
        self._closed = False
        self._old_handler = controller.interactions.handler
        self._handler = self.handle_interaction
        controller.interactions.handler = self._handler

    def display(self, message):
        self.stdout.write(str(self.controller._public(message)) + '\n')
        self.stdout.flush()

    def report_audio_error(self, stage, message):
        self.display(f'[odessy] Audio {stage} unavailable: {message}. Keyboard input remains available; /voice retries audio.')

    def _invalidate_input(self):
        self.input_generation += 1
        self.discard_input(self.stdin)
        self._input_changed.set()

    def _reserve(self):
        # No await between the admission decision and reservation. Controller.run_turn
        # then remains the final authority if another trusted transport races us.
        if self._closing.is_set() or self._reserved or self.controller.running:
            raise ForegroundBusy('Session is busy; use /stop before submitting a replacement.')
        self._reserved = True

    async def submit_text(self, text):
        if not text.strip():
            return False
        try:
            self._reserve()
        except ForegroundBusy as error:
            self.display(str(error))
            return False
        self._turn_stop = asyncio.Event()
        self._turn_task = asyncio.create_task(self._execute(text.strip(), typed=True, stop_event=self._turn_stop))
        return True

    async def submit_voice(self, text, stop_event, context):
        self._reserve()
        self._turn_stop = stop_event
        task = self._turn_task = asyncio.create_task(self._execute(text, typed=False, stop_event=stop_event))
        return await task

    async def _execute(self, text, *, typed, stop_event=None):
        try:
            if stop_event is not None and stop_event.is_set():
                self.display('[odessy] Stopped before dispatch.')
                return ''
            if typed and self.audio is not None:
                await self.audio.set_typed_turn(True)
            if stop_event is not None and stop_event.is_set():
                self.display('[odessy] Stopped before dispatch.')
                return ''
            with self.turn_context():
                # The controller owns context and appends history exactly once.
                result = await self.agent.run_turn(text, stop_event=stop_event, remember=True)
            self.display('Odessy: ' + result.final_text)
            return result.final_text
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.display(f'[odessy] Turn failed: {error}. Completed actions were not retried.')
            return ''
        finally:
            try:
                if typed and self.audio is not None:
                    await self.audio.set_typed_turn(False)
            finally:
                self._reserved = False
                if typed and not self.persistent:
                    self._closing.set()

    def voice_reply_complete(self):
        if not self.persistent:
            self._closing.set()

    async def wait_idle(self):
        task = self._turn_task
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)

    async def stop_turn(self):
        if self._turn_stop is not None:
            self._turn_stop.set()
        if self.audio is not None:
            self.audio.stop_speaking()
        await self.controller.stop()
        await self.wait_idle()

    async def handle_line(self, text, *, generation=None):
        if generation is not None and generation != self.input_generation:
            self.display('[odessy] Discarded stale keyboard input; answer the current prompt.')
            return
        text = text.strip()
        if not text:
            return
        command = text.casefold()
        if command == '/exit':
            self._closing.set()
        elif command == '/stop':
            await self.stop_turn()
        elif command == '/help':
            self.display(self.HELP)
        elif command in ('/text', '/voice'):
            self._voice_enabled = command == '/voice'
            if self.audio is not None:
                if self._voice_enabled:
                    self._schedule_audio_resume()
                    self.display('[odessy] Resuming audio; keyboard input remains available.')
                    return
                if not self._voice_enabled:
                    self.audio.stop_speaking()
                available = await self.audio.set_input_enabled(self._voice_enabled)
                await self.audio.set_output_enabled(self._voice_enabled)
                self.display('[odessy] Voice enabled.' if self._voice_enabled and available else
                             '[odessy] Keyboard input active; voice input unavailable or paused.')
        elif text.startswith('/'):
            self.display('[odessy] Unknown command. Use /help. ' + self.HELP)
        elif self._entry is not None:
            outcome = answer_interaction(self.controller.interactions, self._entry, text,
                                         stop_turn=self.stop_turn)
            if outcome == 'invalid':
                self.display('[odessy] Response not accepted; answer the pending prompt.')
            elif outcome == 'stale':
                self.display('[odessy] That interaction is already resolved or expired.')
        else:
            await self.submit_text(text)

    async def handle_interaction(self, entry):
        self._entry = entry
        self._invalidate_input()
        self.display(format_interaction(entry))
        self.controller.interactions.mark_ready(entry['id'])
        voice = None
        if self.audio is not None:
            voice = asyncio.create_task(self.audio.handle_interaction(entry))
            def voice_done(task):
                if not task.cancelled() and task.exception() is not None:
                    self.report_audio_error('interaction', str(task.exception()))
            voice.add_done_callback(voice_done)
        try:
            # The broker cancels this handler when any valid channel answers or when
            # its existing deadline expires. There is no second keyboard reader.
            await asyncio.Future()
        finally:
            if voice is not None:
                voice.cancel()
                await asyncio.gather(voice, return_exceptions=True)
            if self._entry is entry:
                self._entry = None
                self._invalidate_input()

    async def _start_audio(self):
        if self.audio is None:
            return
        try:
            available = await self.audio.start()
            if not self._voice_enabled:
                await self.audio.set_input_enabled(False)
                await self.audio.set_output_enabled(False)
            if not available:
                self.display('[odessy] Voice input unavailable. Keyboard input is ready; /voice retries audio.')
        except Exception as error:
            self.report_audio_error('startup', str(error))

    async def _resume_audio(self):
        try:
            available = await self.audio.set_input_enabled(True)
            if not self._voice_enabled:
                await self.audio.set_input_enabled(False)
                return
            await self.audio.set_output_enabled(True)
            if not self._voice_enabled:
                await self.audio.set_input_enabled(False)
                await self.audio.set_output_enabled(False)
            else:
                self.display('[odessy] Voice enabled.' if available else '[odessy] Voice input unavailable; keyboard input is ready.')
        except Exception as error:
            self.report_audio_error('startup', str(error))

    def _audio_ready(self, task):
        if self._resume_requested and self._voice_enabled and not self._closing.is_set():
            self._schedule_audio_resume()

    def _schedule_audio_resume(self):
        if self._audio_start is not None and not self._audio_start.done():
            self._resume_requested = True
            return
        self._resume_requested = False
        self._audio_start = asyncio.create_task(self._resume_audio())
        self._audio_start.add_done_callback(self._audio_ready)

    async def run(self):
        if not self.stdin.isatty():
            raise TerminalUnavailable('Keyboard fallback requires an interactive terminal.')
        self.display('[odessy] Keyboard input is available throughout this session. ' + self.HELP)
        self._audio_start = asyncio.create_task(self._start_audio())
        self._audio_start.add_done_callback(self._audio_ready)
        try:
            while not self._closing.is_set():
                self._input_changed.clear()
                generation = self.input_generation
                reader = asyncio.create_task(self.read_line(self.stdin, self.stdout))
                changed = asyncio.create_task(self._input_changed.wait())
                closed = asyncio.create_task(self._closing.wait())
                tasks = (reader, changed, closed)
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    if changed in done or closed in done:
                        continue
                    try:
                        line = reader.result()
                    except (TerminalEOF, TerminalInterrupted):
                        break
                    if line is None:
                        break
                    await self.handle_line(line, generation=generation)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await self.aclose()

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        self._closing.set()
        self._invalidate_input()
        if self._audio_start is not None:
            self._audio_start.cancel()
            await asyncio.gather(self._audio_start, return_exceptions=True)
        try:
            await self.stop_turn()
        finally:
            try:
                if self.audio is not None:
                    await self.audio.aclose()
            finally:
                if self.controller.interactions.handler == self._handler:
                    self.controller.interactions.handler = self._old_handler
