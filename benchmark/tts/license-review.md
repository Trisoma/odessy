# Piper 1.3.0: distribution review and listening preview

Reviewed November 17, 2026 against the installed package's `COPYING` and metadata,
GPLv3 sections 2/4/5/6, and the primary guidance linked below. This is an
engineering compliance assessment, not legal clearance for a final distribution.

## Shipping conclusion

**GPL does permit commercial and free distribution.** Piper 1.3.0 identifies
itself as GPL-3.0-or-later. It is not automatically safe to import it into
Odessy's Apache-2.0 service and distribute that combination as Apache-only.

- Private use/testing does not require publishing Odessy's source or changing
  its license. This standalone listening/benchmark experiment is private use.
- If Piper is combined into one program with Odessy (including a direct library
  import), plan to distribute the covered combination under GPLv3-compatible
  terms, with complete Corresponding Source and required notices. Apache-2.0
  code can participate in a GPLv3 combination, but the combined distribution
  cannot be described as Apache-only. Existing Apache grants/notices remain.
- A collection of genuinely independent programs may qualify as an aggregate,
  letting Odessy retain Apache-2.0 while Piper retains GPL. A standalone engine
  exchanging ordinary text/audio through its documented CLI is a candidate for
  this arrangement. Process separation or optional installation alone is not
  proof: coupling and communication semantics matter. Review the concrete
  adapter and distribution before adopting that route.
- If distributing Piper wheels/binaries, preserve copyright/license notices,
  include the GPL and applicable dependency notices, and provide equivalent
  access to complete matching Corresponding Source, including relevant build
  scripts and modifications. A generic upstream homepage is insufficient.
  A third-party source host can be used under section 6(d), but the distributor
  remains responsible for availability and clear directions beside the download.
- Do not add installer/EULA restrictions that remove the recipients' GPL rights.
  Assess section 6 installation-information duties if shipping in/with a covered
  User Product; a normal desktop download is not automatically such a transaction.
- Audit native dependencies (including bundled eSpeak code/data), voice weights
  and dataset rights separately. A model card's public-domain dataset statement
  is not a complete license declaration for trained weights. Final model-weight
  redistribution provenance/permission remains unresolved.

The tested `en_US-ljspeech-medium` card declares public-domain source data.
The exploratory Lessac card instead links a research dataset license; do not
use it in a release without resolving rights. No speech asset is licensed merely
because the runtime is GPL. Generated audio is not automatically GPL solely
because Piper generated it; separate voice/model rights can still apply.

**Selected direction (user decision, November 17):** Piper replaces Kokoro once
integration and distribution gates pass. Keep this experiment isolated while
designing/reviewing a standalone engine boundary and completing the exact
source/native/model notices before any installer change. No relicensing,
production provider change or distribution is performed by this review.

Sources:

- [GPLv3 text: sections 2, 4, 5 and 6](https://www.gnu.org/licenses/gpl-3.0.html)
- [GNU FAQ: aggregates and combined programs](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)
- [Apache Software Foundation: Apache-2.0/GPLv3 compatibility](https://www.apache.org/licenses/GPL-compatibility.html)
- [Piper 1.3.0 package](https://pypi.org/project/piper-tts/1.3.0/)
- [Tested voice's model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ljspeech/medium/MODEL_CARD)

## Try it without changing Odessy

Piper is Odessy's selected replacement engine. For immediate listening and speed
evaluation, use the isolated preview below. Production adapter/settings and
installer verification belong to the migration checklist, separate from this
standalone test. A model file alone cannot switch synthesizer architectures.

Use the existing isolated benchmark environment instead, from the repository root:

```powershell
& '.qualification/tts-cpu-comparison-20261117/piper-env/Scripts/python.exe' -B benchmark/tts/listen.py --interactive --play --threads 4
```

Type English text, press Enter, and the script synthesizes a WAV and opens it in
the Windows audio player. Empty input exits. It loads the model once, so the
first request is cold and later inputs reuse the session. Output shows model
load, synthesis/WAV time, first returned chunk, audio duration and real-time
factor. Playback occurs after the WAV is finished; this preview does not prove
audible streaming onset. Each WAV has a timing JSON sidecar. No microphone,
provider credential, model download or Odessy restart is needed.

The Desktop's `Odessy-TTS-preview-20261117` folder contains a sample, timing
receipt and a double-click launcher for this same checkout-specific command.
The launcher depends on this repository's existing benchmark environment and
cache; it is not a portable installer. See [CPU results](README.md) for setup,
thread comparisons and cleanup locations. Do not infer Piper packaging from
the existing Kokoro installer path.
