# Starter issue drafts

> **DRAFT — these are proposals, not opened GitHub issues.** No issue was created for
> any entry below. "Suggested label" names a label that would fit; it is not a claim
> that the label exists in this repository — check labels before applying one.
>
> Deliberately excluded: authorization, sandbox policy, credential storage and
> pairing/enrollment code. Those need maintainer review, not a first contribution.
> See the **maintainer review required** rows in [ARCHITECTURE.md](ARCHITECTURE.md).

Repository state these drafts assume: `develop` at the time of writing, status
described in [release notes](releases/v0.5.0-unstable-notes.md). Confirm paths still
exist before filing.

---

## 1. Verify every quick-start command on a clean Windows machine

**Context.** The [from-source quick start](../README.md#from-source-quick-start) is the
only supported install path, but it is maintained by people whose machines already have
Node, Python and a model runtime installed. A single missing step is invisible to us and
fatal to a newcomer.

**Reproduction.** On a Windows 11 x64 machine with no Python, no Node and no Ollama
installed, follow the quick start from a fresh clone and record every command and its
output.

**Files.** `README.md` (section *From-source quick start*), `CONTRIBUTING.md`
(section *Development setup*).

**Acceptance check.** A commit that fixes any wrong or missing command, plus a short
note of the exact Windows version and tool versions used. If all commands work, a PR
that adds the verified versions to the section instead.

**Suggested label.** `documentation`

---

## 2. Add a first-run troubleshooting entry for a failing `doctor` Model check

**Context.** `python -m odessy doctor` reports `[FAIL] Ollama is not reachable at
http://127.0.0.1:11434 ...` when no local model runtime is running. The message is
accurate but the fix path ("install Ollama, or set `ODESSY_MODEL_ENDPOINT`") does not
say what a *cloud* user should do instead.

**Reproduction.** On a machine without Ollama, run `python -m odessy doctor` and read
the Model section.

**Files.** `README.md` (section *Configuration*), `docs/releases/v0.5.0-unstable-notes.md`
(section *Known limitations*, "desktop first run is not guided").

**Acceptance check.** A troubleshooting entry that distinguishes three cases — local
Ollama missing, cloud provider with no key configured, and a configured endpoint that is
unreachable — each with the concrete next command. Must not imply a cloud key is required.

**Suggested label.** `documentation`, `good first issue`

---

## 3. Keyboard-only walkthrough of the desktop settings screens

**Context.** The desktop client is a preview and its accessibility has been sampled, not
audited. Traps in focus order or dialogs that cannot be dismissed from the keyboard are
easy to miss with a mouse.

**Reproduction.** Run the desktop app from source, then reach *Settings* and every
sub-page using only Tab, Shift+Tab, Space, Enter and Escape. Note any control that cannot
be reached, any dialog that cannot be dismissed, and any focus that is lost after a dialog
closes.

**Files.** `apps/desktop/lib/features/*/presentation/`, `docs/ui/design.md`,
`docs/ui/desktop-accessibility-browse-2026-10-28.png`. Read
`apps/desktop/AGENTS.md` first — presentation code is teammate-owned and has a
definition of done.

**Acceptance check.** A written report (screens, steps, expected vs actual) filed as a
new issue, or a PR adding widget tests for focus traversal that currently fails.

**Suggested label.** `accessibility`

---

## 4. Screen-reader pass over the conversation and approval cards

**Context.** Approval is bound to the operation displayed on screen, so a screen-reader
user must be able to hear *which* operation they are approving. If the accessible name of
an approval card omits the command, the safety property is not available to them.

**Reproduction.** With a screen reader on Windows, trigger a confirmation prompt and read
the card. Check whether the announcement includes the tool name and the specific arguments,
and whether approve/reject are distinguishable without colour.

**Files.** `apps/desktop/lib/features/conversation/presentation/`, `docs/ui/design.md`.

**Acceptance check.** A report naming any card whose accessible label omits the operation,
or a PR adding a test that asserts the semantic label includes the tool name.

**Suggested label.** `accessibility`

---

## 5. Offline speech-to-text example that needs no microphone

**Context.** Voice is optional and easiest to get wrong on first contact. A pre-recorded
WAV path proves the adapter works without hardware or a paid provider.

**Reproduction.** Run the installed `ggml-tiny.en.bin` Whisper adapter against a short
16 kHz mono WAV and record the transcript. The October 23 qualification shows the expected
shape and its limits: three repetitions of a phrase were returned in 1.89 s, and
"approve this action" was transcribed as "Approve this option."

**Files.** `src/odessy/voice/`, `docs/reference/speech-providers.md`,
`docs/setup/jetson-voice-preview.md`.

**Acceptance check.** A docs page or example script that runs offline on a WAV and prints
the transcript, with the exact adapter configuration and the model file the writer used.
Must state that transcription accuracy is not a support claim.

**Suggested label.** `documentation`, `voice`

---

## 6. Text-to-speech example that renders to a file instead of a speaker

**Context.** TTS is the last hop of the intended combined experience, and it is the one
most likely to fail silently on a headless box. Rendering to a file removes the need for
audio hardware.

**Reproduction.** Configure the Kokoro adapter and synthesize one short sentence to a WAV
file on a machine with no audio output device.

**Files.** `src/odessy/voice/`, `docs/reference/speech-providers.md`,
`docs/qualification/2026-10-23-jetson.md` (the word-count mismatch warning is documented
there and its cause is uninvestigated).

**Acceptance check.** A runnable example plus the exact command and the resulting file
duration, and an explicit note about which assets must be downloaded and under which
licence terms.

**Suggested label.** `documentation`, `voice`

---

## 7. Add a build-evidence table for Windows from source

**Context.** "Builds on Windows" is currently a claim with no reproduced record. A small
table removes the guesswork and gives reviewers something to diff when it breaks.

**Reproduction.** From a fresh clone on Windows 11 x64, record the tool versions and the
exit code of each documented step (`npm ci`, `npm run build`, `python -m pip install -e
".[test,ui]"`, `flutter pub get`, `cargo build --locked`).

**Files.** `CONTRIBUTING.md` (section *Checks*), `.github/workflows/ci.yml`,
`.github/workflows/desktop-preview.yml`.

**Acceptance check.** A table of step → tool version → exit code, with the Windows build
number, committed to the docs. Do not record a step as passing if it was skipped.

**Suggested label.** `documentation`, `build`

---

## 8. Make `benchmark/` emit a copy-pasteable hardware report

**Context.** The hardware report template asks for throughput, latency, counts and OOM
status. Today a reporter has to transcribe those by hand from raw output, which invites
transcription errors and makes reports hard to compare.

**Reproduction.** Run a `benchmark/` scenario with `--adapter real` and compare what it
prints with the fields the template requires.

**Files.** `benchmark/`, `.github/ISSUE_TEMPLATE/hardware_report.yml`,
`docs/qualification/2026-10-23-jetson.md`.

**Acceptance check.** Either a `--report` output that contains every template field, or a
documented mapping from benchmark output to template fields. A `--adapter fake` run must be
clearly marked as a harness check, not a hardware measurement.

**Suggested label.** `benchmark`, `good first issue`

---

## 9. Document the desktop first run, start to first answer

**Context.** The release notes say the desktop first run is not guided: you must configure a
model in Settings before anything works. That is a known gap and a good place for a
written walkthrough with screenshots.

**Reproduction.** From a fresh clone, start `python -m odessy serve`, launch the desktop
client, configure a model, and get one answer — noting every screen and every dead end.

**Files.** `README.md` (section *Desktop first run*), `apps/desktop/`, `docs/ui/README.md`,
`docs/ui/design.md`.

**Acceptance check.** A numbered walkthrough with screenshots of each step and the exact
model configuration used, plus an explicit statement of what still has no guidance
(pairing, devices, monitoring).

**Suggested label.** `documentation`, `desktop`
