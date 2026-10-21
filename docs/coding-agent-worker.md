# Coding agent stdio worker

Enable collaboration in the machine's user configuration, configure local coding
participants and their workspaces, then launch:

```shell
odessy coding-agent-server
```

This explicit command admits the stdio operator as an execution caller. It serves
MCP over stdin/stdout only; it opens no HTTP listener. Connect it only through an
already trusted execution target. A caller can approve delegation for the
capabilities and modes the operator configured. Model arguments cannot supply an
approval flag or expand those settings.

The worker offers `model.consult` when a participant allows consultation and
`agent.delegate`, `agent.job_status`, and `agent.cancel` when a participant allows
delegation. Each request still checks the selected participant's capabilities,
modes, and canonical local workspace. Advice never forwards to another host, so a
worker cannot create a chain of consultation workers. On stdin EOF the worker
cancels outstanding children; patch worktrees remain available for inspection.

Codex advice requests its read-only sandbox. Claude Code advice requests its
read tools only. Child CLIs retain their own permissions and authentication;
configured custom commands need their own controls. Job `permissions` metadata
reports requested restrictions with `enforcement: child_cli_not_verified`.
Coding CLI usage remains unknown and outside Odessy's cloud API budget.

Controllers preserve a remote workspace string for validation on that worker's
OS. A lost remote connection leaves the job in `needs_attention` and retains its
workspace reservation. An explicit status refresh must verify remote completion
before another job can use that reservation. Reservations span managers in the
same controller process; restarting that process clears its local reservation
registry and requires the operator to check the remote host before resubmitting.


Adapter command references: Codex flags were checked against the installed
`codex exec --help`. Claude flags follow the [official CLI reference](https://code.claude.com/docs/en/cli-reference).
These checks do not establish live account compatibility or child confinement.
