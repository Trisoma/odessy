# Local terminal and workspace checkpoints

**Developer Preview · v0.5.0-alpha**

## Interactive terminal versus recorded Output

Output displays bounded, session-owned tool observations and accepts no shell input.
Opening or restoring Output never executes a command. A local interactive terminal
is a distinct capability backed by the owned Node helper and a real native PTY.

The production composition registers terminal routes only when the verified helper,
Node runtime and matching native dependency closure are available. The reviewed
dependency receipt pins `node-pty 1.1.0` and `node-addon-api 7.1.1` with integrity
and license records. Native Windows ConPTY tests and prior installed checks exist
for recorded snapshots; they do not qualify a new final release candidate.

The service capability response is authoritative. Missing closure means unavailable;
there is no fixture fallback, remote SSH terminal or automatic package download.
Creation requires an explicit exact shell grant through existing policy/broker
ownership. A shell runs with the user's OS permissions: its input is not individually
intercepted or confined to the selected workspace. Do not grant it for untrusted work.

Routes are scoped to `/v1/sessions/{sid}/terminals`: create, read bounded ordered
output, submit input, resize, interrupt and close. Session/service identity,
revision, current authority and handle ownership remain checked. Revocation and
session/service shutdown close owned helpers. Restart never restores handles,
replays input or resumes a shell.

## Checkpoints

Checkpoints use a private Git index and owned `refs/odessy/checkpoints/*` refs.
Metadata lives outside the workspace. They do not reset the user's index, branch
or unrelated dirty files. Git must already be available; non-Git roots are unsupported.

Bounds: 1,000 files, 2 MiB per file, 10 MiB total captured text and 20 retained
checkpoints per workspace. Sensitive paths, Git internals, links/reparse points,
NUL/non-UTF8/binary content and unsupported files are excluded and reported.
This is not a general backup or full-filesystem restoration service.

Creation and restoration use the existing explicit approval owner. Preview binds
the checkpoint, workspace, current content hashes and revision. Changed content
or a stale revision requires a new review; opening a preview grants no permission.
Restoration takes a safety checkpoint first and writes only supported captured
paths, atomically per file. It is not a multi-file atomic transaction and cannot
undo network calls, executed commands or other external effects.

## Windows restore safety

**Checkpoint restoration works on Windows** through handle-anchored replacement.
Python on Windows has no `dir_fd`/`O_NOFOLLOW`, so the POSIX no-follow defense does
not port directly. Instead, `application/checkpoints/win32_replace.py` binds the NT
primitives: each path component is opened relative to the already-verified parent
handle with `FILE_OPEN_REPARSE_POINT`, a junction/symlink is refused at every level,
and the final replace is an `NtSetInformationFile(FileRenameInformation)` rename
relative to that verified handle. No path string is re-resolved after validation, so
a concurrent directory swap cannot redirect the write outside the workspace.

Creation, listing, preview and restore all function. If the NT bindings are
unavailable on a host, restore returns HTTP 501 `unsupported` before approval,
safety capture or workspace mutation; there is no unsafe path-based fallback.

Preview replies include `restore_available` and `restore_unavailable_reason`.
Clients should present the live capability rather than a fixed Windows restriction:
the connected service's host and bindings decide availability, not the client's OS.
Existing binaries/packages must be rebuilt from this source to contain the
handle-anchored implementation; earlier installed receipts do not prove it.

## Ownership and verification

`application/basics_integration.py` owns production composition; session lifecycle
owns cleanup; terminal/checkpoint services use trusted inspector/policy/broker
callbacks. Feature availability is not inferred from a rendered widget.

Source checks include `tests/test_checkpoint_service.py`,
`tests/test_agent_basics_production.py` and `tests/test_terminal_native.py`.
Native terminal checks require the reviewed helper/runtime closure and are separate
from ordinary offline fixtures. See [release verification](../release-verification.md) for candidate acceptance requirements.
