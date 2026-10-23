# Third-party software and model licenses

Reviewed October 23, 2026 against upstream notices, project manifests, the Node
lockfile and locally installed package/crate metadata. Odessy's original source
uses Apache-2.0; that license does not replace dependency or model licenses.
This inventory covers the named runtime integrations and direct dependencies,
not a complete platform-specific transitive-dependency or binary compliance audit.
Exact installed versions, build options and downloaded assets remain relevant.
Reference license texts and their download sources are retained in
[`THIRD-PARTY-LICENSES/README.md`](../../THIRD-PARTY-LICENSES/README.md).

## Voice: code and weights are separate artifacts

| Component | License | What Odessy uses / distribution notes |
|---|---|---|
| [openWakeWord code](https://github.com/dscripka/openWakeWord#license) | Apache-2.0 | Optional wake detector library. Preserve its license/notices when redistributing. |
| [Pretrained Hey Jarvis weights](https://huggingface.co/davidscripka/openwakeword) | CC-BY-NC-SA-4.0 | `hey_jarvis_v0.1.onnx`, explicitly downloaded by setup. Attribution, noncommercial and applicable ShareAlike terms apply. Public availability does not make the weights Apache-licensed or unrestricted for commercial use. |
| openWakeWord auxiliary models | Asset-specific | Track mel-spectrogram, embedding and VAD assets separately. Upstream identifies the shared Google feature backbone as Apache-2.0, while its general pretrained-model notice is CC-BY-NC-SA-4.0. Preserve exact file/release provenance; do not infer a blanket license for every ONNX file. |
| [Silero VAD](https://github.com/snakers4/silero-vad/blob/master/LICENSE) | MIT upstream | Speech endpointing through openWakeWord's wrapper. Preserve the Silero notice for the verified upstream artifact; also retain the distributor's provenance/notices for a repackaged model. |
| [Whisper code and original model weights](https://github.com/openai/whisper#license) | MIT | Includes the original tiny.en model used for STT. Converted/quantized files must retain original notices and identify their conversion source. |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp/blob/master/LICENSE) | MIT | External inference executable; its license is separate from Whisper weights and any optional acceleration dependencies. |
| [Kokoro-82M weights and official voice assets](https://huggingface.co/hexgrad/Kokoro-82M) | Apache-2.0 | Official model card and voice files are the provenance for the configured Kokoro assets. Preserve the model card's attribution, including its Koniwa/SIWIS training-data acknowledgements. Custom voices have their own provenance. |
| [kokoro-onnx wrapper](https://github.com/thewh1teagle/kokoro-onnx#license) | MIT | Python wrapper; upstream separately identifies the Kokoro model as Apache-2.0. Record the source/version of `kokoro-v1.0.int8.onnx` and `voices-v1.0.bin`; quantization is not a new license grant. |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) | MIT | Runtime code; preserve its packaged third-party notices as well. GPU providers add dependencies. |
| [phonemizer](https://github.com/bootphon/phonemizer/blob/master/phonemizer/__init__.py) | GPL-3.0-or-later | Used by the default kokoro-onnx dependency path; do not describe the complete voice stack as permissively licensed. |
| [espeakng-loader](https://github.com/thewh1teagle/espeakng-loader/blob/main/LICENSE) | MIT loader | Loader licensing does not replace the license of its bundled/loaded eSpeak NG library and data. |
| [eSpeak NG](https://github.com/espeak-ng/espeak-ng#license-information) | GPL-3.0-or-later, plus component notices | Phonemization dependency. Retain licensing and fulfill applicable source/distribution obligations for redistributed GPL components. |
| [SoundFile](https://github.com/bastibe/python-soundfile) / libsndfile, if installed | BSD-3-Clause wrapper / LGPL library | Optional audio tooling; inspect the exact wheel/native library and its included notices rather than assuming the wrapper license covers everything. |

The reviewed kokoro-onnx 0.6.1 manifest explicitly depends on `phonemizer` and
`espeakng-loader`; the laptop environment contains both. Misaki or another
configured phonemizer does not automatically remove those installed dependencies.
Bundling, importing or linking GPL components requires review of the actual
distribution; this document neither relicenses Odessy's source nor asserts that
an Apache-only bundled voice distribution is cleared.

The wake setup manifest records asset URL, checksum and declared license. It does
not inventory every manually supplied voice/VAD file. For packaged redistribution,
record each asset's upstream URL, release/revision, SHA-256 and license/notice.
Externally installed components still have license terms even when not bundled.

## Local language models and cloud services

| Component | License / terms | Boundary |
|---|---|---|
| [Ollama runtime](https://github.com/ollama/ollama/blob/main/LICENSE) | MIT | Does not license the models it downloads. |
| [LFM2.5-1.2B-Thinking](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Thinking/blob/main/LICENSE) | LFM Open License v1.0 | Custom terms, including a commercial-use revenue threshold defined in the license. Not Apache-2.0. Preserve terms/notices for Q8/Q6 conversions and derived Modelfiles; an alias does not change the model license. |
| [Gemma 3](https://ai.google.dev/gemma/terms) | Gemma Terms of Use and applicable prohibited-use terms | Custom model terms, separate from Ollama. Retain the exact downloaded model's license; do not substitute an Apache license from a Gemma code repository. |
| Cloud APIs and Codex/Claude Code or other external agents | Provider/account/service terms | API adapter licensing does not grant rights to provider models or replace service terms. Review the chosen account and integration; no cloud weights are included by configuring an endpoint. |

## Python direct dependencies

Declared in `pyproject.toml`; extras are optional. The linked upstream project and
the exact installed distribution's license files are authoritative.

| Dependency | License |
|---|---|
| [mcp](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) | MIT |
| [jsonschema](https://github.com/python-jsonschema/jsonschema/blob/main/COPYING) | MIT |
| [tomli](https://github.com/hukkin/tomli/blob/master/LICENSE) | MIT |
| [anthropic SDK](https://github.com/anthropics/anthropic-sdk-python/blob/main/LICENSE) | MIT |
| [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) | MIT |
| [Uvicorn](https://github.com/encode/uvicorn/blob/main/LICENSE.md) | BSD-3-Clause |
| [NumPy](https://github.com/numpy/numpy/blob/main/LICENSE.txt) | BSD-3-Clause core; wheel includes additional component licenses |
| openwakeword, kokoro-onnx, onnxruntime | See the voice table above |
| pytest / AnyIO / HTTPX (test extra) | MIT / MIT / BSD-3-Clause |
| setuptools (build backend) | MIT |

## TypeScript tool host

`tools/package-lock.json` records resolved package versions and license metadata.
Direct runtime dependencies `@modelcontextprotocol/sdk`, `koffi` and `zod` are MIT.
Development dependencies: TypeScript is Apache-2.0 and `@types/node` is MIT.
Node.js itself includes MIT and bundled third-party notices; preserve the notices
of the exact runtime if distributing it. Transitive dependencies retain their own
licenses; this direct-dependency table is not a replacement for their notices.

## Rust host transport

The cached crate metadata reviewed for the native transport declares:

| Crate | Declared SPDX expression |
|---|---|
| mdns-sd 0.13.11 | Apache-2.0 OR MIT |
| rcgen 0.13.2 | MIT OR Apache-2.0 |
| rustls 0.23.46 / rustls-pemfile 2.2.0 | Apache-2.0 OR ISC OR MIT |
| tokio 1.54.1 | MIT |
| tokio-rustls 0.26.6 | MIT OR Apache-2.0 |
| rusqlite 0.32.1 | MIT |
| serde 1.0.229 / serde_json 1.0.154 | MIT OR Apache-2.0 |
| sha2 0.10.9 / time 0.3.55 | MIT OR Apache-2.0 |
| ring 0.17.14 (transitive crypto provider) | Apache-2.0 AND ISC; retain bundled component notices |

`OR` offers a license choice; `AND` requires compliance with both. Inspect the
locked crate graph and its license/notice files for each binary release, including
platform dependencies such as libc and bundled SQLite. SQLite's upstream core is
[public domain](https://www.sqlite.org/copyright.html); that does not replace the
licenses of the Rust wrapper/build dependencies.

## System tools and accelerator libraries

ALSA/PulseAudio, optional FFmpeg/PyTorch and NVIDIA CUDA/cuDNN/TensorRT are external
components with their own package/build-specific terms. FFmpeg licensing varies by
enabled features; NVIDIA acceleration libraries are not covered by Odessy's Apache
license. Include their exact notices/redistribution terms if bundling them. Merely
using a configured executable path is not evidence that it is shipped in Odessy.

## Existing adapted code and release packaging

OpenClaw and DeepSeek Harness adaptations are MIT; NemoClaw adaptations are
Apache-2.0. Their original notices and license copies remain in
[`THIRD-PARTY-NOTICES.md`](../../THIRD-PARTY-NOTICES.md) and
[`THIRD-PARTY-LICENSES/`](../../THIRD-PARTY-LICENSES/). Existing attribution is
retained. Hermes remains an architecture-only reference in that notice.

Before distributing a complete installer/binary bundle, inventory its resolved
Python/Node/Rust dependencies, system libraries and downloaded model assets;
include required license/NOTICE texts and fulfill any source obligations. A
source inventory is not a declaration that every possible bundle is cleared.
Future Flutter/Dart dependencies belong to the separate desktop branch's package
inventory and must be included when v0.5.0 packaging is defined.
The planned v0.5.0 Go monitoring service also needs a dependency/license inventory
when modules and distributed collectors are chosen; it is not present in v0.4.0.

## Wake-word roadmap

v0.4.0 uses **Hey Jarvis**. A custom **Odessy** detector is deferred to v0.5.0,
including training, pronunciation/false-activation tests and a training-data/TTS
asset license review. Training a new classifier does not automatically remove
restrictions inherited from its inputs or bundled feature-extractor assets.
