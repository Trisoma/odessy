# Preview API contract

All paths require `Authorization: Bearer <memory-only token>`. The client accepts
loopback origins only and refuses redirects. A selected device is never used as
an implicit request target. There are no direct provider/native calls in widgets.

| Operation | Existing backend route |
|---|---|
| List profiles | GET `/v1/profiles` |
| Create/refresh/close | POST `/v1/sessions`, GET/DELETE `/v1/sessions/{sid}` |
| Next-turn selection | PUT `/v1/sessions/{sid}/model`, `{profile}` |
| Submit once | POST `/v1/sessions/{sid}/turns`, `{text}` |
| Stop | POST `/v1/sessions/{sid}/stop` |
| Events | GET `/v1/sessions/{sid}/events?after_id=N` |
| Approval | POST `/v1/sessions/{sid}/approvals/{id}`, `{approved: bool}` |
| Clarification | POST `/v1/sessions/{sid}/questions/{id}`, `{answer: string}` |
| Collaboration settings | GET/PUT `/v1/settings` |

The backend assigns response-channel identity. The client never sends a channel,
risk, permission, action modification or model-written authorization. Interaction
cards render the backend request and exact arguments. Pending IDs, expiry and
allowed UI channels gate response controls; backend validation remains authoritative.
HTTP 404/409/410 are stale/unavailable outcomes, never success.

On SSE loss, the client reloads the existing session snapshot and reconnects using
the latest event sequence. It never resends a turn, approval or tool operation.
Bounded SSE frames (2 MiB) and HTTP JSON bodies (4 MiB) fail explicitly.

The current backend does not expose service-instance identity, settings revisions,
desktop voice or host management. The origin is the connection label; reconnect
to a restarted service may need an explicit new connection. It does not silently
create a replacement session or pretend the old one survived.

## Local setup model catalog (additive)

The fixed-operation setup bridge accepts `operation: model-catalog` with
`parameters: {provider, endpoint?, credential_env?, refresh?}`. Credentials remain
in the helper environment; only a variable reference is passed. `refresh` defaults
to false and returns local provider-default suggestions without contacting a server.
True explicitly requests model metadata, never inference or a model download.

Result: `{status, models: [{name, label}], error?}`. `name` is the exact saved API
identifier; `label` is presentation text. Suggestions are not account availability
claims. Results cover one bounded page, at most 256 entries / 1 MiB, with a 3-second
read budget and no redirects. Errors never include provider response bodies or keys.
Providers without listing support retain manual input. Claude uses its Models API;
OpenAI-compatible providers use `/models`; local providers reuse Ollama `/api/tags`.

`SetupMetadataBackend.createMetadataReader()` optionally supplies an independent
read-only helper; existing backend implementations remain compatible. The model
catalog controller suppresses stale replies after provider changes or disposal.

## Independent local component settings

The local fixed-operation helper accepts `operation: component-settings` and
`parameters: {action, root?, section?, patch?, revision?, confirm?}`. Actions are
`load`, `review`, `save`. Load returns the current full selection, workspace and
opaque revision. Section keys are allowlisted: `llm`, `stt`, `tts`, `voice`, `host`.
Review validates and merges only the selected section and returns a `plan_id`.
Save requires that exact section patch, revision and confirmation. Inputs changing
between review/save or another persisted edit invalidate confirmation.

No HTTP configuration endpoint or remote administration capability is introduced.
Flutter calls typed controllers through the existing local stdio setup bridge.
The existing Python session/settings API retains its behavior. Managed installations
reuse the configuration engine; its approval now also binds the previous active
configuration, preventing a concurrent edit from being silently overwritten.
Source settings use create-only JSON exports and the existing owned desktop pointer
with a writer lock; interrupted source writer locks require inspection, never blind
replay/removal. JSON exports remain schema version 1. No old export is overwritten.

Save returns `saved: true, restart_required: true` and the updated snapshot. Speech
components can be staged with `voice.enabled: false`; this does not activate them.
Authentication references remain names of environment variables, never key values.
A section update cannot include another section's fields. Path checks do not execute
providers. Portable role/platform restrictions and execution-host authority remain
in force. A legacy or ambiguous installation is rejected rather than migrated.
