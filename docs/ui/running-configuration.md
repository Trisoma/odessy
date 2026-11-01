# Saved settings, startup configuration and recovery

October 27, 2026 — Phase 2 software implementation. This extends the existing
settings/launch engines; it does not add a second policy or maintenance engine.

## What the labels mean

The coordinator captures an immutable configuration descriptor at startup. Saved
settings are read separately through the existing native helper. Matching hashes
mean the configuration arguments match, **not** that inference, tools, audio or
provider credentials have been tested.

| Label | Evidence |
|---|---|
| Matches running configuration | Same configuration owner and section fingerprint as the connected service startup snapshot. |
| Saved · pending restart | Same owner, different saved section fingerprint. |
| Different configuration owner | The connected service does not describe this source/installation owner. |
| Running configuration unknown | Optional descriptor is unavailable, unsupported or unverified. |
| Running configuration unknown · disconnected | Transport/session is disconnected; saved files cannot establish current runtime state. |

Assistant identifies the **startup model** separately from next-turn selection and
per-turn routing. Profiles may select another model; startup provenance is not a
claim about every subsequent generation. Settings retain independent section saves.

Canonical per-section hashes cover LLM selection, STT, TTS, voice enablement and
host/workspace. The total fingerprint additionally binds the owner. Installed
ownership includes the verified installation ID and canonical root; source ownership
uses the remembered-state location, remaining stable across immutable export files.
Actual credential contents are never inputs or response fields. Paths/credential
references are configuration, not permission or successful dependency checks.

## Additive authenticated API

- `GET /v1/capabilities`: schema version 1, process-unique service instance ID,
  optional startup `configuration`, and observational `restart_safe`.
- `POST /v1/lifecycle/prepare-restart`, body `{service_instance_id}`: rechecks idle
  sessions, pending turns/admissions, interactions and background jobs; atomically
  reserves admission and returns an in-memory ticket. New turns, sessions and job
  submissions are rejected while reserved. Reservation expires after 60 seconds.
- `POST /v1/lifecycle/resume`, body `{ticket}`: releases a matching reservation
  after cancellation/failure. No rejected request is replayed automatically.

These use the existing loopback bearer authentication and bounded client transport.
Tickets never enter URLs, ordinary settings or logs. `RuntimeConfigurationApi` is
an optional Dart interface; old/custom adapters and HTTP 404 remain supported as
unknown state. A manually composed server without a verified launch selection
reports unknown configuration instead of guessing from current saved files.

**Apply saved settings · restart** reviews startup/saved model and workspace,
rechecks the saved revision, reserves the original service, and restarts only the
desktop-owned coordinator. Source and installed native entrypoints verify the
expected total fingerprint again before launch, refusing changed settings/owners.
Unowned services must be restarted through their owner. Existing Connect controls
cannot bypass this path for a live coordinator. Completed tools remain completed;
idle conversation/session resources close. No model pull or inference test is run.

## Conflict and crash recovery

Review against latest values presents bounded **Saved / Your edit** values and
overlapping changed fields. Accepting a merge updates only the draft, preserves
fresh untouched fields, binds owner/revision/edit state and requires a separate
Save. Backend revision and exact review confirmation remain authoritative.

Structured field errors are additive to old helper reasons. Only selected-provider
asset paths are checked/shown; inactive paths are preserved. Provider modules are
not imported or executed to render/validate settings.

Source saves use an owned journal plus an OS advisory lock. A killed/failed writer
retains pending state, visible when configuration is reopened. Inspection acquires
the lock, reads the current verified pointer and supplies an exact recovery plan.
Explicit recovery acknowledges that pointer and clears only the owned pending
journal. It does not replay a save, remove immutable exports, delete incomplete
files or guess that an old empty/unowned lock is safe. Active writers and changed
recovery reviews are refused. Installed/portable recovery remains with their
existing installation-management engines. Normal desktop closure resolves drafts
and drains writes before stopping its owned coordinator; forced exit has no such
unsaved-draft guarantee.

## Verification boundaries

See the October 27 daily log and parent execution plan for final counts. Tests
include source process A remaining A after B is saved, startup fingerprint rejection,
authenticated restart admission, background-job refusal, stale observations, review
merges, source writer kills before/after pointer activation and preservation of
unowned locks. Common portable contracts are exercised with filesystem fixtures.

Cloud credential entry remains **advanced preconfigured use**: the named environment
variable must already be available to the desktop process. No GUI secret entry or
durable OS vault is claimed. Real STT/TTS/provider integration remains the teammate's
task; no paid inference or physical audio was run here. Native Computer Use was
unavailable (pipe OS error 2). A Windows build and automated helper tests do not
establish clean-machine packaged, Linux/macOS/ARM64 or physical-device acceptance.
