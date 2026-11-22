# Applying voice settings

The desktop saves STT, TTS, wake-word and audio-device settings through the existing
configuration owner. A running installed or configured coordinator reloads those
voice fields without replacing conversations, model configuration or workspace.

- Idle audio: the saved configuration becomes active without restarting Odessy.
- Recording, transcription, playback or wake listening: application is deferred.
  Stop the active audio operation (disable wake listening when enabled); the
  coordinator applies the saved settings once audio is idle.
- The Settings save message reports applied, deferred or unconfirmed application.
  An application failure does not undo the saved settings or imply audio works.
- Model, workspace and installation changes retain their separate restart rules.
- No microphone, playback, model inference or downloads start merely from saving.

`POST /v1/voice/configuration` accepts only the exact saved configuration
`fingerprint`, requires ordinary service authentication and reads the coordinator's
trusted configuration location. It accepts no provider paths or executable payload
from HTTP. A stale receipt returns 409; an older service returns unavailable.

This change must be delivered as updated application code. Restarting an older
binary cannot add hot configuration support. Use a verified versioned upgrade;
do not patch installed owned files by hand or bypass their digest inventory.
