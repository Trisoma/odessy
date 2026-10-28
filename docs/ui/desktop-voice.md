# Desktop push-to-talk — implementation and acceptance boundaries

October 27, 2026. This is a tested software increment, not full voice/product release
acceptance. It uses the existing session, broker, provider factories and HybridAudioAdapter.

## Usage and contract

Enable existing voice components in Settings and explicitly restart the owned
coordinator. No microphone opens on application startup. The desktop's microphone
control records on the **coordinator computer**, whose hostname is displayed; it
does not upload laptop audio to a remote Jetson. Finish submits the transcript
once; Cancel submits nothing. Typed requests cancel obsolete capture, preserve the
same session/history/model selection and retain normal policy/budget checks.

- Authenticated `GET /v1/sessions/{sid}/voice` reports supported/state/capture ID,
  recorder host and a safe message. Older/unconfigured services remain unavailable.
- Authenticated `POST` accepts `start_capture`, `finish_capture`, `cancel_capture`,
  `stop_playback`, `test_input`, `test_output`. Post-start capture operations require
  the backend-assigned capture ID. Pending interaction capture additionally binds
  the exact expected question/approval ID at start.
- SSE `voice_state`, `voice_transcript`, `voice_test_result`, `audio_error` accompany
  existing session/turn/interaction events. Transcripts pass through session redaction.
- Test input uses capture/finish without a model turn. Test output speaks a fixed
  sample only after explicit selection. Tests use the configured provider; no silent
  provider fallback or automatic model/tool retry is introduced.

One service-wide audio lease prevents concurrent sessions opening the same device.
PCM is bounded to at most 30 seconds and 16 kHz mono 16-bit. Recorder startup,
recognition and synthesis run off the event loop; providers remain cached. Capture
settles before a fresh recorder opens; listening is not admitted during playback.
Cancellation invalidates generation and retains the lease until owned work drains.
Stalled capture is not reused while its reader is still draining. A global banner
keeps active audio visible and cancellable when navigating away from Conversation.

Spoken interaction answers use the existing broker. Positive permission remains
exactly `approve this action`; bare yes is rejected. Natural negative/stop phrases
reuse existing parsing. High-risk/expanded-sandbox requests require the stronger
channel. Expired/resolved/superseded questions cannot be answered by old recordings.
No model output is interpreted as permission.

## Workspace and device changes

Workspaces now distinguishes observed running folder from saved host configuration.
Live Devices overview reads monitoring inventory keyed by opaque host ID, displays
trust/connectivity/telemetry separately, and links to existing performance inspection.
Monitoring-only or online hosts do not gain execution permission. Existing native
pairing remains a separate explicit route; this is not a new pairing protocol or a
complete polished enrollment wizard.

## October 27 scope snapshot (superseded by the October 28 software checkpoint)

- Actual recorder/microphone/device permission and provider qualification on each
  supported OS. The default recorder/player remain existing arecord/aplay-compatible
  commands; this does not establish a native Windows/macOS microphone backend.
- Echo/pre-cue behavior on actual hardware, reconnect/physical unplug and complete
  packaged voice/tool/approval/clarification acceptance.
- Voice tests in independent Settings without an active coordinator, richer recorder
  and output-device selection, provider capability/status UX, and desktop wake mode.
- Full readiness matrix/model-test evidence, activity presentation and complete
  pairing/revocation/device-management journeys from the overarching plan.
- Clean-machine final portable/installer artifacts, native platform/service behavior,
  assistive-technology validation and testing by unfamiliar external users.

Fake audio/API coverage establishes software boundaries, not those native gates.
The original UX plan remains open. No production-ready or all-platform claim follows
from a Windows build or the passing suites alone.

## October 28 software checkpoint

Independent Settings probes, native audio/device selection, opt-in desktop wake,
pairing/revocation presentation and provider-test metadata are now implemented.
Native audio uses the optional sounddevice/PortAudio backend; Linux command audio
remains supported. No microphone opens merely by opening Settings or listing
devices. Diagnostic tests require consent and operate without a coordinator.
See [the closeout evidence](backend-acceptance-2026-10-28.md) and
[the additive contract](desktop-ux-contract.md#october-28-parallel-implementation-contract).
The historical omissions above are not current software omissions; physical and
native release acceptance remains distinct from these automated checks.
