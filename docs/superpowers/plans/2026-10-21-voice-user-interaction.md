# Risk-aware voice approval and clarification implementation

**Goal:** implement the user-approved risk-aware spoken approval and mid-task
clarification plan on an isolated branch, retaining existing policy boundaries.

**Base:** develop 7438379; worktree odessy-voice-interaction;
branch feat/voice-user-interaction. Installer status, tracked diff and untracked
SHA-256 snapshot compared identical before/after creation. Never merge/push.

## Constraints
- Voice is an intent channel, not identity authentication. Medium confirmation
  may use voice; high risk or sandbox expansion requires terminal/authenticated UI.
- Deterministic descriptions and one-use action/host/arguments/scope/policy binding;
  combined prompts retain both ordinary confirmation and escalation validation.
- user.ask accepts question, optional choices, allow_free_text=true. Controller
  creates IDs/expiry. Clarification never authorizes anything.
- One foreground interaction per turn; background jobs, progress, API remain live.
- Exact spoken approval, no bare yes/session grants/batch all. Distinct prompts,
  playback/pre-cue suppression, settling=300ms; 30s normal/120s stronger responses,
  two STT retries; default three model clarification requests per turn.
- Preserve text CLI and existing approval API; add question API and diagnostics.
- Stop/interrupt/shutdown expire pending work; first valid transport answer wins.

## Tasks and ownership
- [x] Broker, shared session lifecycle, question/approval API and diagnostic client.
- [x] Deterministic approval plans, canonical binding and policy preflight.
- [x] Wake/one-shot voice response capture, cancellation-safe keyboard adapter.
- [x] Runtime combined approval, user.ask native tool/schema, composition/CLI.
- [ ] Offline tests, docs, independent review and final installer snapshot comparison.

## Verification
Test risk/channel enforcement, forged descriptions, mutation and replay, combined
gates/auditing, concurrent responses/jobs, clarification continuation and budgets,
timeouts/interruption, fake microphone/playback failures and terminal cancellation.
Run affected suites then full offline suite. Real hardware/provider tests opt-in.

## Review corrections
- MCP clarification errors now stop dependent batch calls instead of resuming.
- Revalidate grants after both ordinary and namespaced transport setup; keep
  authority refusals outside MCP task-group exception wrapping.
- Isolate controller turns from the persistent wake worker; Stop can be followed
  by a fresh request. API turn IDs match interaction ownership IDs.
- Diagnostic reconnect restores pending controls from a session snapshot before
  replay. Concurrent replies remain first-valid-wins.
- Delegated jobs preflight configured participant/host/workspace before asking;
  changed profiles or targets invalidate direct API and model-initiated approval.

## Installer preservation
Creation-time status, tracked diff and untracked-file hashes matched exactly.
A final read-only comparison found additional concurrent installer/test changes
in the original checkout, which were left untouched. Untracked hashes still
match, including installer/ui_theme.py:
`d1963aedc82f7d9a326308ebbfb593b8e2ef15c8e72f77155225496db0434ec4`.
