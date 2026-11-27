# Conversation and workspace client integration

**Developer Preview · v0.5.0-alpha**

The production application exposes saved history and advanced inspector panels
through the existing authenticated service owner. The runtime capability response,
not panel visibility, decides which operations are available.

## Behavior and ownership

- Saved projects/conversations support title, pin/archive, revision-bound deletion,
  bounded export, search and pagination. Opening history does not create a runtime.
- Custom continuation creates a fresh controller with matching trusted workspace
  binding; Harness restart history remains read-only. No transcript replay,
  cross-engine migration or restored grants are provided.
- Mode controls show the last service acknowledgment, effective scope and errors.
  Stale identities/revisions cannot receive new actions. Jobs, Handoffs, terminal
  creation and checkpoint restoration retain independent approval boundaries.
- Context shows what was actually prepared. Explicit memory and document indexing
  require their real services; source deletion/revocation excludes affected data.
- Terminal is separate from recorded Output. Opening either starts no process.
  Available terminal controls create, submit exact input, refresh bounded output,
  resize, interrupt and close; no terminal-emulator or remote-PTY parity is claimed.
- Checkpoints require reviewed preview hashes/revisions and an actual service
  acknowledgment. Restore availability is reported by the service (including on
  Windows, where handle-anchored replacement is used when its NT bindings exist);
  see [checkpoint availability](terminal-checkpoints.md).

`AgentBasicsClient` owns typed versioned parsing over `LocalApi`; `BasicsController`
owns state/revisions/capability and epoch guards; `BasicsPage` owns presentation.
Widgets open no databases, workspace files, sockets or processes. The shared
shell/session owners preserve drafts, next-turn model selection and voice lifecycle
while switching panels. Project metadata does not replace configured roots, runtime
history or permissions. Existing Activity & usage, Jobs/Handoffs and settings remain reachable.

Production `PersistentComposition` mounts the services and owns trusted callbacks.
Conversation deletion invalidates conversation-owned derived data; it does not
delete arbitrary source documents, independent project memory or unrelated files.
Session/service close hooks release ephemeral terminal resources. No handle or
command input is replayed on reconnect or restart.

## Verification

Use the [contributor checks](../../CONTRIBUTING.md#desktop-and-dart-client).
`agent_basics_client_ux_test.dart` covers revisions, pagination, identity guards,
read-only states and layouts. `agent_basics_connected_preflight_test.dart` uses
the production shell with authenticated loopback Python, an isolated profile and
an explicitly deterministic model. Those are source tests, not real-provider,
native PTY, installed-package, hardware or human acceptance.
