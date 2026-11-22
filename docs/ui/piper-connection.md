# Connect an installed Piper engine

**Developer Preview · v0.5.0-alpha**

For a fresh installation, choose **Piper** and enable the optional speech
download in setup. Setup downloads the pinned engine and English voice, installs
the engine in Odessy's active Python runtime, checks model loading, and saves the
paths automatically. Existing files can still be selected manually below.
See [voice components in setup](../setup/voice-components.md) for storage,
verification and license details.

Settings → Text to speech supports the built-in `piper` provider. Set the
provider to `piper`, browse to a trusted Piper CLI executable (or the Python
interpreter in its installed environment), and select its ONNX voice model.
Keep the matching `<model>.onnx.json` beside the ONNX file. Detect existing
components also checks the persistent Odessy Voice directory's `piper` folder.
Detection observes files; it does not run engines or silently save selections.

Save, then run the explicit speech-output test. Idle main-panel audio receives
the saved selection through the existing configuration-refresh owner. Active
recording, playback and wake listening retain their protection; stop them before
a deferred change applies. STT and wake settings are separate and are retained.

The common TTS interface remains `synthesize(text, output_path)` with provider
cancellation/close hooks. This connector starts the selected external program
with a fixed argument list, sends plain text on stdin, validates its WAV, and
publishes output only on success. Stop/timeout terminates its owned process tree;
failed, cancelled or malformed output cannot replace an existing output file.
The connector never imports Piper into the Odessy process, downloads packages,
or substitutes fixtures. Custom `package.module:factory` providers and existing
Kokoro configurations remain supported.

This initial external CLI connector starts an engine per synthesis request;
it does not claim warm-session latency or word-level streaming. The selected
Piper CLI controls inference providers and thread settings; this CPU-tested
connection does not claim GPU qualification. A local repeat through the connector
took about 2 seconds including process startup on the evaluated laptop; initial
startup was about 14 seconds. These differ from warm in-memory benchmark timings.

The engine and voice remain optional resources. The setup executable contains
their pinned download manifest; after consent it fetches the wheel and model
directly from upstream, rather than embedding them in the installer payload.
The earlier local evaluation
uses a persistent `<Voice>/piper/runtime` and `<Voice>/piper/models` directory,
outside versioned app files. See the [license review](../../benchmark/tts/license-review.md)
before distributing an engine, model or combined installer; process separation
alone does not settle GPL or model redistribution obligations. Downloaded models
and metadata live in the persistent Voice directory; automatic setup uses the
active Odessy Python environment for the engine.
