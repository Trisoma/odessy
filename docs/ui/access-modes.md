# Session access modes (backend)

Server-side authority for how much confirmation an action needs. The mode is stored by the
Odessy service per live session; it is **not** a client flag and cannot be granted by opening a
panel, reading history or reconnecting.

## Modes

| Mode | Wire value | What it controls |
| --- | --- | --- |
| Ask every time | `ask` | Every authorizable side effect asks for confirmation. Ordinary policy *allows* — harmless reads — are not turned into prompts. Default. |
| Approve for me | `auto` | The service runs a real deterministic review (`policy-v1`). It approves exactly one bounded class of action (below) and asks for everything else. |
| Full control | `full` | Suppresses routine *authorization-only* confirmations inside the current scope. Escalations, hook confirmations and hard denials still stop. |

Scope is the pair `{workspace_id, target_id}` resolved from the live session by a trusted
callback. Without a provable live scope no mode is reported `effective`, and setting a
non-default mode is refused with `409`. Existing confinement, sensitive-path denials, workspace
grants and paired MCP credentials in `execution_access` are untouched by every mode.

Revision starts at `0` (mode `ask`) and increases by one on every accepted change. A change
requires `expected_revision`; a mismatch is `409` and leaves the old mode in place. Closing or
restarting the session discards the mode and any pending decision — a restart returns to `ask`
revision `0`, and previously issued decisions never validate.

## Routes

`GET /v1/sessions/{sid}/access-mode`

```json
{
  "version": 1,
  "session_id": "…",
  "service_instance_id": "…",
  "mode": "ask",
  "revision": 0,
  "supported_modes": ["ask", "auto", "full"],
  "reviewer": "policy-v1",
  "scope": {"workspace_id": "…", "target_id": "…"},
  "effective": true
}
```

`PUT /v1/sessions/{sid}/access-mode`

```json
{"mode": "auto", "expected_revision": 0, "service_instance_id": "…"}
```

Returns the same snapshot. `scope` is `null` when no live scope can be proven; `effective` is
then `false`. Engine support is honest: the Custom/`legacy` engine reports all three modes; the
Harness engine reports `supported_modes: []`, `effective: false`, and a mutation is `501` until a
real Harness decision adapter exists. No mode is silently mapped to a weaker or stronger one.

Status codes: `400` invalid body (unknown fields, missing fields, non-integer or boolean
`expected_revision`, unknown mode), `401` missing bearer, `404` unknown session, `409` stale
revision, service-identity change, or no live scope, `501` engine without a mode adapter,
`503` unavailable owner. Errors use `detail: {code, message}`.

## Reviewer (`policy-v1`)

Input is the frozen `ReviewInput`, built by the trusted dispatcher **after** existing policy,
hooks and the exact `ActionApprovalPlan`. It is never parsed from HTTP or redacted UI text.

| Decision from existing policy | `ask` | `auto` | `full` |
| --- | --- | --- | --- |
| `deny` | deny | deny | deny |
| `allow` | approve | approve | approve |
| `confirm` | ask | approve only the bounded class, else ask | approve authorization-only, else ask |

The bounded class for `auto` is exactly all of:

- `operation == "filesystem.write"`;
- `rule == "workspace_write"`;
- `risk == "medium"`;
- `scopes == ("authorization",)`;
- `content_bound_review == true` (the exact before/after content was reviewed);
- `scope_authorized == true` and `current_binding == true`;
- the decision's `mode_revision` equals the current revision and the live scope equals the scope
  bound when the mode was set.

`full` asks instead of approving when the plan includes `escalation`, when the rule is a hook
(`hook:*`), when any scope other than `authorization` is present, or when the binding, scope or
revision is stale. It is not a blanket approve.

Every result records outcome, reason, rule, operation, risk, scopes, fingerprint and revision in
a bounded in-memory audit (`AccessModeService.records()`); no raw arguments or secrets are kept.

## Dispatch barrier and revocation

1. The dispatcher screens the action with existing policy/hooks and builds the exact plan.
2. It calls `review(ReviewInput)` and gets `approve`, `ask` or `deny`.
   - `approve` continues without a prompt.
   - `ask` uses the existing broker confirmation flow.
   - `deny` refuses; the action never runs.
3. Immediately before dispatch it calls `validate(result, input)` with the same plan.

`validate` re-checks the live session, service identity, current mode revision, current scope and
the recorded fingerprint. It **consumes** the grant, so one approval authorizes exactly one
dispatch; replaying a decision, reusing it after a mode change, after `close()`, in another
session, or after the workspace/target moved all fail.

Changing the mode therefore invalidates decisions prepared under the previous revision. A turn
already running keeps the decisions it already validated; the next dispatch uses the new mode.
Nothing about history, reconnect or opening a page can authorize work.

## Integration (owned by 06)

- Construct one `AccessModeService(dependencies, resolve_scope=…)` and pass it both to
  `access_mode_routes.register(app, dependencies, service=service)` and to the dispatcher, so a
  set mode actually governs actions.
- `resolve_scope(session_id, controller)` must return the live authorized
  `{'workspace_id', 'target_id'}`, or `None`.
- Call `service.review(...)` after screening and `service.validate(...)` at the last dispatch
  barrier in both engine adapters; call `service.close(sid)` when a session closes or restarts.
- Flip the `access_modes` capability only when both routes and the barrier above are mounted and
  tested end to end.

## Limits

- Mode state is in memory, per live session: it is not durable configuration and does not
  survive a service restart. That is intentional — restart must not restore authority.
- The reviewer is deterministic and offline. It never calls a model, so "approve for me" cannot
  silently become an always-approve oracle; anything it does not recognise asks.
- `auto` deliberately covers one class of ordinary workspace writes. Terminal commands,
  delegation, hooks and escalation always ask in `auto`.
- This document describes the service contract only. No UI in this pack is wired by this change.
