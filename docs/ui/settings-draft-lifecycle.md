# Settings drafts and save recovery — October 27, 2026

This increment follows the navigation foundation. It changes Flutter settings
ownership, not Python persistence, provider behavior, security policy or wire formats.

## Ownership

- The application owns one `ComponentSettingsController` for its native backend
  and installation root. Settings and Workspaces borrow it; leaving either view
  never disposes the only owner of an admitted save.
- `SectionDraft` holds baseline values, edited values and the baseline revision.
  Model, STT, TTS, voice and host drafts remain separate. A refresh does not silently
  rebase a dirty draft; the backend still rejects its stale reviewed revision.
- Collaboration uses the existing `SettingsFormController`, retained by the app
  per API object. Changing service cannot apply a previous service's draft.
- Drafts are memory-only. They are not a durable journal, credential vault or
  claim that unsaved edits survive a hard process kill.

## User behavior

Leaving a dirty editor offers **Save / Discard / Keep editing**. Failed Save keeps
the draft and the current view. Discard only affects that editor. Explicit Reload
asks before replacing edits with saved values. Duplicate navigation events cannot
stack competing prompts. A clean component explains why Save is disabled.

An admitted save may finish while the user navigates elsewhere. Reopening the
editor sees its retained result. Normal app closure waits for owned saves before
closing transports. Connection replacement and configuration activation are blocked
while settings operations are pending. Completed writes are never undone by Stop.

Component save sequence remains the existing **review → exact confirmation → save**
with the original baseline revision, section and installation root. No automatic
retry or touched-field merge is introduced. A stale draft requires explicit reload
and re-edit against the fresh values.

If a save response is lost or fails after dispatch, the result is **unknown**, not
Not saved or Saved. Editing/resubmission is blocked until inspection or explicit
reload. **Inspect saved values** performs only `load`: matching values can be
acknowledged as observed persistence; different values keep recovery blocked until
the selected section is reloaded. Inspection does not prove which writer saved
those values and never replays the mutation. Collaboration failures similarly
require explicit reload before another save.

## Verified scope and remaining gates

New tests cover all three leave choices, pending saves across navigation/disposal,
unknown outcomes and read-only inspection, owner separation, retained baseline
revisions, collaboration edits, and preservation of unrelated settings.
`settings_bridge_integration_test.dart` drives the real local Python helper for
source and versioned installations, saving/reopening TTS without an LLM and proving
revision conflicts. It requires an explicit isolated test home and interpreter;
it must never mutate a developer's real configuration.

Provider-specific STT/TTS integration and execution verification belong to the
teammate's separate task. This increment touches shared editor lifecycle, not
provider adapters. No real provider, audio or model inference was performed.

Follow-up implementation now supplies startup/saved comparison, field errors,
explicitly reviewed draft merges, owned source-writer recovery and forced-kill
tests around pointer activation. See [running configuration and recovery](running-configuration.md).
Merges still require a new Save; they are never automatic. Existing unowned locks
and incomplete files are never deleted by this UI. Native OS, clean-machine
packaged and physical acceptance remain separate from helper/widget coverage.
