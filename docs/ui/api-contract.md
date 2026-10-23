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
