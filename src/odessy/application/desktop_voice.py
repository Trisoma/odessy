"""Explicit, leased desktop voice over the existing session authority."""
from __future__ import annotations

import asyncio
import contextlib


class VoiceBusy(RuntimeError):
    pass


def _default_factory(controller, submit, report):
    from odessy.voice.config import VoiceConfig
    from odessy.voice.hybrid import HybridAudioAdapter
    return HybridAudioAdapter(config=VoiceConfig.from_env(), submit_voice=submit,
                              stop_turn=controller.stop, report_audio_error=report,
                              memory=controller.memory)


class DesktopVoice:
    """One owner per service. No microphone opens until an explicit start.

    A short lease closes the owned adapter if the desktop stops heartbeating.
    Model work and approvals remain owned by the existing session controller.
    """
    def __init__(self, *, factory=None, lease_seconds=20):
        self.factory = factory or _default_factory
        self.lease_seconds = lease_seconds
        self._lock = asyncio.Lock()
        self._owner = self._adapter = self._start_task = self._watchdog = None
        self._state = 'off'
        self._failed_owner = None
        self._expires = 0.0
        self._generation = 0

    def status(self, controller):
        if self._owner is not controller:
            return {'state': 'unavailable' if self._failed_owner is controller else 'off',
                    'input_available': False, 'output_available': False}
        adapter = self._adapter
        return {'state': self._state,
                'input_available': bool(adapter and adapter.input_available),
                'output_available': bool(adapter and adapter.output_available)}

    def _emit(self, controller):
        controller.emit('voice', self.status(controller))

    async def start(self, controller, *, reserved=lambda: False):
        async with self._lock:
            if self._owner is not None:
                raise VoiceBusy('Voice is already active or starting')
            if controller.running or reserved():
                raise VoiceBusy('Wait for the current turn to finish')
            generation = self._generation = self._generation + 1
            loop = asyncio.get_running_loop()

            async def submit(text, stop_event, context):
                if self._owner is not controller or controller.running or reserved() or stop_event.is_set():
                    return ''
                try:
                    result = await controller.run_turn(text, context=context, stop_event=stop_event)
                    return result.final_text
                except Exception:
                    return ''  # Controller emits failure; do not replay a turn.

            def report(stage, _message):
                # Do not expose device diagnostics or paths in the UI event.
                def deliver():
                    if self._owner is not controller or self._generation != generation:
                        return
                    controller.emit('voice_error', {'stage': stage if stage in
                        {'capture', 'wake', 'stt', 'synthesis', 'playback'} else 'audio'})
                    if stage in {'capture', 'wake', 'stt'} and self._state == 'active':
                        self._state = 'unavailable'
                        self._emit(controller)
                        asyncio.create_task(self.stop(controller, failed=True))
                if not loop.is_closed():
                    loop.call_soon_threadsafe(deliver)

            adapter = self.factory(controller, submit, report)
            self._failed_owner = None
            self._owner, self._adapter, self._state = controller, adapter, 'starting'
            self._expires = asyncio.get_running_loop().time() + self.lease_seconds
            self._start_task = asyncio.create_task(self._open(controller, adapter, generation))
            self._watchdog = asyncio.create_task(self._lease(controller, generation))
            self._emit(controller)
            return self.status(controller)

    async def _open(self, controller, adapter, generation):
        try:
            ready = await adapter.start()
            if self._generation != generation or self._owner is not controller:
                return
            if not ready:
                self._state = 'unavailable'
                self._emit(controller)
                await self.stop(controller, failed=True)
                return
            self._state = 'active'
            self._emit(controller)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._generation == generation and self._owner is controller and self._state != 'stop_failed':
                self._state = 'unavailable'
                self._emit(controller)
                with contextlib.suppress(Exception):
                    await self.stop(controller, failed=True)

    async def _lease(self, controller, generation):
        try:
            while self._generation == generation and self._owner is controller:
                remaining = self._expires - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await self.stop(controller)
                    return
                await asyncio.sleep(min(remaining, 2))
        except asyncio.CancelledError:
            pass
        except Exception:
            # A failed close is reported in status; no automatic turn is retried.
            pass

    async def heartbeat(self, controller):
        async with self._lock:
            if self._owner is not controller:
                raise VoiceBusy('Voice is not active for this session')
            if self._state == 'stop_failed':
                raise VoiceBusy('Voice shutdown failed')
            self._expires = asyncio.get_running_loop().time() + self.lease_seconds
            return self.status(controller)

    async def set_typed_turn(self, controller, active):
        if self._owner is controller and self._adapter is not None:
            await self._adapter.set_typed_turn(active)

    async def stop(self, controller=None, *, failed=False):
        async with self._lock:
            owner, adapter = self._owner, self._adapter
            if owner is None:
                if controller is None or self._failed_owner is controller:
                    self._failed_owner = None
                return
            if controller is not None and owner is not controller:
                return
            if self._state == 'stop_failed':
                raise VoiceBusy('Voice shutdown failed; restart the local service')
            self._generation += 1
            self._state = 'stopping'
            self._emit(owner)
            current = asyncio.current_task()
            for task in (self._start_task, self._watchdog):
                if task is not None and task is not current and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            try:
                await adapter.aclose()
            except Exception:
                self._state = 'stop_failed'
                self._emit(owner)
                raise
            self._owner = self._adapter = self._start_task = self._watchdog = None
            self._state = 'off'
            self._failed_owner = owner if failed else None
            self._emit(owner)
