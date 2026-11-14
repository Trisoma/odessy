# Local terminal and workspace checkpoints

## Terminal (B13) — unavailable in this build, deliberately

There is **no interactive terminal** here, and nothing pretends otherwise. A real local
PTY needs a reviewed native dependency or binding; this repository has no cached
`node-pty`, `@lydell/node-pty`, `koffi`, `winpty` package and no reviewed ConPTY adapter.
Downloading one requires separate authorization, so:

- no terminal route is registered;
- `TerminalService.capability()` returns
  `{available: false, reason: <specific>, dependency: ..., recorded_output_is_not_terminal: true, remote_terminal: false}`;
- every operation (`create`, `output`, `input`, `resize`, `interrupt`, `close`) raises
  `PtyUnavailable` (HTTP `501`), so a client can never render a shell that cannot run.

**Recorded command output is not a terminal.** The inspector's Output tab shows the
public observation of one settled `terminal.run` call. It cannot accept input, resize,
interrupt or start a process. No remote terminal is in scope.

The method set matches the frozen contract so the integrator can wire routes against one
object if a PTY is later authorized; until then the capability stays `false` and the
reason string is user-visible copy, not a debug message.

## Checkpoints (B14) — workspace file snapshots from local Git

A checkpoint captures workspace files so an agent's file changes can be previewed and
reversed. It is implemented with a **private temporary Git index** and **owned refs**
under `refs/odessy/checkpoints/*`:

- the user's index, staging area, branches and working tree are never reset, cleaned or
  checked out;
- metadata lives in an owned JSON file **outside** the workspace
  (`state_dir/<workspace-hash>/checkpoints.json`) with a revision and atomic replace;
- creating a snapshot requires an explicit broker grant (`checkpoint.create`); restoring
  requires a fresh one (`checkpoint.restore`) and is refused while authorization is
  unwired (`501`).

### Limits (honest, enforced)

| Bound | Value |
| --- | --- |
| Files per checkpoint | 1000 |
| Single file | 2 MiB |
| Total captured bytes | 10 MiB |
| Retained checkpoints per workspace | 20 |
| Label | 1–120 printable characters |

Sensitive paths (`.env`, `*.key`, `*.pem`, `*secret*`, credential files, `.git`
internals), symlinks, non-files and paths a policy rejects are **excluded and listed as
unsupported** — they are not captured, so a restore cannot revert them either. A snapshot
with nothing capturable fails `413` instead of recording an empty promise.

### Restore protocol

1. `GET /preview` returns `changes` (`modified` / `missing` / `unchanged`), `unsupported`,
   `expected_revision` and a `preview_hash` that binds the **current content** of every
   captured path.
2. `POST /restore {preview_hash, expected_revision}`:
   - stale `expected_revision` → `409`;
   - any content change since the preview (same status, different bytes included) → `409`
     “review again”;
   - grant refused/unwired → `403`/`501`, nothing written;
   - binding revalidated immediately before the effect;
   - a **safety checkpoint** (`pre-restore …`) is taken first, and its failure aborts;
   - only captured paths are rewritten, atomically per file. Untracked, unrelated,
   sensitive and unsupported files are left exactly as they are.
3. The response reports the safety checkpoint so the user can undo the undo.

Named checkpoints are never deleted implicitly except by retention (oldest beyond 20,
which removes only our ref and metadata entry). `delete` requires the same grant.

### Not claimed

- Non-Git workspace roots are **unsupported** (`501`, explicit reason). They get no
  partial copy-based promise.
- Rollback restores files. It does not undo external effects: commands already run, network
  calls, or anything a terminal action did (and there is no terminal here).
- File mode bits, empty directories, symlinks and binary content beyond the size bounds are
  outside the guarantee.
- Snapshots are local to this computer; nothing is uploaded.

## Integration (owner 07)

- One `CheckpointService(inspector=app.state.workspace_inspector,
  request_grant=<controller.confirm_action wrapper>, state_dir=<app config>/checkpoints)`
  shared by the routes and any dispatcher check.
- `request_grant(sid, plan)` must use the live controller's existing broker with an exact
  plan (`action`, `session_id`, `scopes: ['authorization']`, plus label/id/preview hash).
- Register with `checkpoints.routes.register(app, dependencies, service=...)`; keep the
  `checkpoints` capability `false` until the route suite passes in the integrated tree.
- Keep the `terminal` capability `false` and expose `PTY_REASON` as copy.
