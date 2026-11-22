# Desktop voice

**Developer Preview · v0.5.0-alpha**

Voice uses the existing session/controller, model selection and approval broker.
It is an optional input/output path, not a separate authority or conversation engine.

## Configure and use

The Windows installer offers Piper (recommended for CPU) or Kokoro, with optional
speech downloads and a separate wake-model choice. It can install Piper from scratch;
you do not need a previously installed engine. See the [installer steps](../GOLDEN_PATH.md#1-install-and-open-odessy).
For existing installations, select compatible voice components in Settings. Saved voice settings apply to idle
audio without restarting; active recording, playback or wake listening defers the
change until audio is idle. See [live settings](live-voice-settings.md).
Opening Settings, listing devices or starting
the application does not authorize microphone capture. The microphone control
records on the coordinator computer identified by the UI; it does not implicitly
upload local audio to a remote computer.

Finish submits the transcript once; Cancel submits nothing. Typed input and voice
share the session, history and next-turn model selection. Stop requests cancellation
and does not roll back completed actions. Spoken approval expresses intent, not
authenticated identity, and cannot replace required exact high-risk authorization.

## Settings and diagnostics

Speech to text, Text to speech and Voice remain separate settings groups. Native
device selection, explicit provider probes and opt-in wake controls use the
existing controllers. Diagnostic capture or playback requires consent. Test success
does not prove that every microphone, audio driver, model or wake configuration works.

Provider dependencies and assets are optional and retain their own licenses.
No model or microphone access is required for the [text-only contributor start](../getting-started.md).
See [voice components](../setup/voice-components.md),
[provider reference](../reference/speech-providers.md) and
[voice integration](../reference/voice-integration.mdx).

## Acceptance boundaries

Software regressions and previous scoped device tests do not establish final
installed-package acceptance, acoustic echo/interrupt behavior, assistive-technology
acceptance or all-platform support. Keep errors and unavailable capabilities visible;
never substitute fake audio or claim successful hardware capture from a widget test.
See [current support](../current-status.md).

## Speech latency and acceleration

Spoken main-panel turns consume genuine model-text sentences as they arrive.
Providers without text deltas retain final-answer playback. Text-only turns do
not start speech. Playback uses short sentences/phrases and prepares at most one
phrase ahead; Stop invalidates remaining speech. This preserves natural phrasing
instead of making one synthesis request per word.

The external Piper runtime controls its own acceleration/thread settings; the
current candidate uses the verified CPU-compatible runtime. Odessy's GPU fallback
settings below describe retained Kokoro support, not a Piper GPU guarantee.

Kokoro's automatic ONNX selection prefers a compatible installed accelerator and
falls back to CPU when accelerator initialization fails. A GPU alone is not
enough: its ONNX provider, drivers and model operators must be supported. No driver
or GPU dependency is downloaded automatically. See [performance and verification](voice-performance.md).
