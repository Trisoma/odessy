# Harness voice source integration

Verified on November 11, 2026. This extends Odessy's existing voice/wake path;
it does not replace its providers, devices, permissions or installer.

- Harness worker-thread deltas feed the existing sentence stream. Retry/tool
  boundaries, settled-answer deduplication and stale-turn rejection are covered.
- Wake barge-in uses the existing controller Stop implementation.
- Phase timing separates capture, recognition, engine text and playback request.
  A settled answer is timestamped before push-to-talk playback, so playback time
  cannot inflate engine latency or create a negative playback interval.
- `first_audio_measurement: playback-request` explicitly identifies the software
  boundary. Device-level audible onset is not measured; failed playback retracts
  the request mark. Timing snapshots contain no transcript or audio.

Validation: 283 passed, 2 skipped across voice runtime events, wake session,
desktop voice/API, voice integration/interaction/loop/native startup/path checks,
speech providers, combined interaction, wake VAD, cloud resilience and application
session/API/bootstrap. The new HTTP regression uses a deterministic clock and
checks answer availability before playback and separation from playback duration.

The final source also passed `smoke_p13_voice.py`: 12/12 against the pinned
0.2.1rc2 runtime (`f9ed01f2...13365`) and packaged bridge, using a fresh isolated
home/work directory and deterministic provider. This covered live sentence
delivery, no settled-answer replay and confirmed Stop with no subsequent speech.
The first sandboxed attempt failed with Windows realpath EPERM; the successful
rerun ran outside that sandbox. Neither run used a microphone or actual playback.

Push-to-talk retains settled-answer playback. Real microphone sensitivity,
post-cue immediate speech, hardware interruption, installed-app voice,
non-Windows devices and live-provider latency remain unverified. Source tests and
the deterministic pinned-runtime smoke do not qualify those checks. Packaging
must include this revision's application source before installed qualification.
