"""Reload only voice settings from the coordinator's trusted persistence owner."""
import asyncio
from dataclasses import replace

from .capabilities import configuration_descriptor


class LiveVoiceConfiguration:
    def __init__(self, voice, selection, workspace, owner, load):
        self.voice = voice
        self.selection = selection
        self.workspace = workspace
        self.owner = owner
        self.load = load
        self.lock = asyncio.Lock()
        self.error = ''
        self._refresh_pending = False
        self.voice.enabled = selection.voice.enabled

    async def refresh(self, app, expected=None):
        async with self.lock:
            saved = await asyncio.to_thread(self.load)
            descriptor = configuration_descriptor(saved, self.workspace, owner=self.owner)
            if expected is not None and descriptor['fingerprint'] != expected:
                raise ValueError('Saved settings changed. Reload before applying audio.')
            # Save is also an explicit recovery request when the selected names
            # are unchanged but Windows reconnected/replaced a device.
            if expected is not None:
                self._refresh_pending = True
            if saved.voice != self.selection.voice or self._refresh_pending:
                if self.voice.busy:
                    return {'status': 'deferred', 'message': 'Saved. Audio settings will apply when recording, wake listening and playback are stopped.'}
                from odessy.voice.config import VoiceConfig
                from odessy.voice.desktop_audio import DesktopAudio
                config = VoiceConfig.from_selection(saved.voice)
                self.voice.reconfiguring = True
                try:
                    if saved.voice.enabled:
                        from odessy.voice.native_audio import refresh_devices, AudioBackendBusy
                        try:
                            await asyncio.to_thread(refresh_devices, config.audio_backend)
                        except AudioBackendBusy:
                            return {'status': 'deferred', 'message': 'Saved. Native audio is still draining; settings will apply when idle.'}
                    await self.voice.close()
                    self.voice.factory = lambda: DesktopAudio(config)
                    self.voice.audio = None
                    self.voice.capture_id = None
                    self.voice.expected = None
                    self.voice.state = 'idle'
                    self.voice.error = ''
                    self.voice.enabled = saved.voice.enabled
                    self.selection = replace(self.selection, voice=saved.voice)
                    self._refresh_pending = False
                finally:
                    self.voice.reconfiguring = False
                app.state.runtime_configuration = configuration_descriptor(
                    self.selection, self.workspace, owner=self.owner)
                for controller in tuple(getattr(app.state, 'sessions', {}).values()):
                    self.voice._emit(controller)
            self.error = ''
            return {'status': 'applied', 'message': 'Saved audio settings are active.',
                    'configuration': app.state.runtime_configuration}

    async def watch(self, app):
        while True:
            await asyncio.sleep(0.5)
            try:
                await self.refresh(app)
            except Exception:
                # Keep the last valid configuration, never claim a failed reload applied.
                self.error = 'Saved audio settings could not be applied. Reload Settings and retry.'
