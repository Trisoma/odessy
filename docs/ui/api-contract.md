# Desktop API contract

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
| Guarded collaboration limit | GET/PUT `/v1/settings/consultation-limit` |
| Guarded collaboration context limit | GET/PUT `/v1/settings/context-limit` |
| Guarded collaboration round limit | GET/PUT `/v1/settings/round-limit` |
| Guarded collaboration availability | GET/PUT `/v1/settings/collaboration-enabled` |
| Existing model profile editor | GET `/v1/settings/model-profiles`, PUT `/v1/settings/model-profiles/{name}` |
| Model profile management | GET `/v1/settings/model-profile-providers`, POST `/v1/settings/model-profiles`, DELETE `/v1/settings/model-profiles/{name}` |
| Cloud provider change | PUT `/v1/settings/model-profiles/{name}/provider` |
| Cloud profile pricing | GET/PUT `/v1/settings/model-profiles/{name}/pricing` |
| Cloud credential reference | GET/PUT `/v1/settings/model-profiles/{name}/credential-reference` |
| Model request limits | GET/PUT `/v1/settings/model-profiles/{name}/limits` |
| Runtime setting inventory, read-only | GET `/v1/runtime-settings` |
| Session job summaries | GET `/v1/sessions/{sid}/jobs?limit=50&after=<cursor>` |
| Job detail, submission and cancellation | GET/POST `/v1/sessions/{sid}/jobs[/{job_id}[/cancel]]` |
| Monitoring health/storage | GET `/v1/monitoring` |
| Monitoring inventory/detail | GET `/v1/monitoring/hosts`, GET `/v1/monitoring/hosts/{id}` |
| Monitoring metrics/history | GET `/v1/monitoring/hosts/{id}/metrics`, GET `/v1/monitoring/hosts/{id}/history` |
| Monitoring events | GET `/v1/monitoring/events`, `Last-Event-ID: epoch:sequence` |

Session snapshots include `available_profiles`, the model names accepted by that
session's model selector. The desktop falls back to the global profiles endpoint
for older services that omit this field. Existing sessions keep their configuration
snapshot; profile edits apply to newly created sessions.

Model request limits use the shared settings instance and revision. Timeout is
1–3600 seconds and maximum output tokens is 1–1000000; updates apply to new sessions.
The context limit is 1–100000 characters, uses the same revision and applies to
new sessions; editing it does not enable collaboration.
The round limit is 0–32, shares the revision, and constrains each turn alongside
the separate consultation count; the lower limit takes effect.
Collaboration availability applies to new sessions. Existing automatic consultation
settings and approval policies remain in force when collaboration is enabled.

Monitoring uses `LocalApi.createMonitoringClient()` with the same immutable origin
and private in-memory bearer. Complete pinned inventory pagination before event
subscription; `resync_required` discards the replay position and reloads inventory.
Trust/access events also invalidate older in-flight snapshots. Permission denial
hides cached readings and invalidates pending reads. Controller results bind to
connection, host and requested history range; device inspection changes no session
execution target. Monitoring response/frame limits are 2 MiB. History uses repeated
`series` query parameters, not a comma-separated query value. See the
[monitoring-v1 handoff](../monitoring/api.md) for bucket and retention semantics.
The Python public route preserves all repeated `series` values when forwarding
to the Go service; other query fields retain their existing handling.
These routes add no pairing, trust or configuration mutations. HTTP 503 stays
unavailable while conversation APIs remain usable; it never activates demo data.

The backend assigns response-channel identity. The client never sends a channel,
risk, permission, action modification or model-written authorization. Interaction
cards render the backend request and exact arguments. Pending IDs, expiry and
allowed UI channels gate response controls; backend validation remains authoritative.
HTTP 404/409/410 are stale/unavailable outcomes, never success.

The runtime inventory requires authentication and reports the server instance ID,
registry metadata and whether each process-environment variable is nonempty. It
returns no configuration values, including for non-secret fields, and supports no
mutation. Presence does not establish the active runtime value, validity or file
source. Changing a service or session connection invalidates the old response.

The job-list route requires the bearer and a live session, returns only
that session manager's `job_id`, `participant`, `mode`, `state` and
`remote_state_unknown` fields, and does not submit or refresh jobs. It returns at
most 50 entries in insertion order plus a last-item cursor when more exist. Pages
are not frozen snapshots: newly added jobs can appear on subsequent pages. A cursor
must identify a job in the same manager; invalid parameters/cursors return 400,
an unknown session returns 404, and an unconfigured manager returns 503. Details,
logs and task contents remain accessible only through the existing job-by-ID route.
The desktop submits once and leaves the session approval broker available while
waiting for a response. A lost or unconfirmed submission is never replayed; the
user must inspect activity and explicitly acknowledge the uncertain result before
another submission. This work remains local; project-owner review is deferred at
the user's request.

On SSE loss, the client reloads the existing session snapshot and reconnects using
the latest event sequence. It never resends a turn, approval or tool operation.
Bounded SSE frames (2 MiB) and HTTP JSON bodies (4 MiB) fail explicitly.

The collaboration limit route requires the instance ID and revision read from the
same service. Writes through either settings route advance its revision; a stale
guarded write fails with HTTP 409. This protection is scoped to one service
process: legacy whole-document writes and external file edits are not guarded
against overwriting later data. Desktop live connections refuse to fall back to
unguarded writes on older services. The model profile editor shares that revision:
it reads only model, adapter, provider and endpoint for existing cloud/Ollama
profiles, updates only model and endpoint, and does not expose credentials. The
service validates endpoint URLs. Cloud/Ollama profiles can be created or explicitly
deleted; reserved names and references from collaboration rules/defaults are
protected. Provider catalog entries expose only the expected environment variable
name, never its value. Changes apply to newly created sessions, not the currently
connected session. A cloud provider change requires a different registered
provider, replaces model and endpoint from explicitly submitted fields, clears
profile-specific pricing and refuses profiles with a custom credential reference;
the new provider uses its own default credential environment variable. Pricing
uses explicitly entered per-million-token input, cached input and output rates;
an absent price remains unknown. Guarded updates require one currency across
configured profile and global prices. Price changes require service restart because
the process usage ledger retains pricing for the lifetime of the service. Credential
reference management reads and writes only an environment variable name; it never
returns or stores the secret value. Blank selects the provider default. Custom
names must end in `_API_KEY` or `_ACCESS_TOKEN`, with internal `ODESSY_` names
reserved except `ODESSY_CLOUD_API_KEY`. The secret must already exist in the
service environment, and changing that environment may require a service restart.
Changes apply to new sessions. The
runtime inventory exposes an instance ID,
but session and other settings contracts do not yet use it to guard reconnections
or writes. The backend also lacks settings
revisions and host management. Desktop voice uses the session-scoped authenticated
`voice`, `voice/start`, `voice/heartbeat`, and `voice/stop` routes. Start is
explicit; the service permits one owner and closes its existing audio adapter when
the desktop's 20-second lease expires. Heartbeats run every five seconds while
the conversation page owns voice. Leaving the page requests a stop; reconnection
does not automatically restart it. The adapter still requires compatible local
audio programs and configured voice assets; native Windows microphone support
has not been established. The origin is the connection label; reconnect
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
