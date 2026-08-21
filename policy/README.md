# Odessy Policy

This folder documents the policy boundary used by the Python Odessy runtime.
The model proposes a tool call, but it cannot authorize or execute that call.

## Authority Flow

```text
model proposal
  -> tool-contract validation
  -> workspace/path authority
  -> policy decision
  -> human confirmation when required
  -> MCP transport
  -> tool execution
```

The Python runtime is the authority. MCP transports authorized requests but does
not replace Python policy.

## Current Outcomes

### ALLOW

Used for low-risk operations such as:

- `time.now`
- `calculator.eval`
- approved read-only filesystem and project-search tools
- `git.status`, `git.diff`, `git.changed_files`, and `git.log`
- safe read-only commands such as `pwd`, `ls`, `dir`, and `git status`
- `log.append`, whose destination is fixed inside the internal Odessy log area

### CONFIRM

Used for operations that may modify state or exceed the normal execution scope,
including:

- `filesystem.write`
- write-like or installer commands
- non-read-only Git commands
- commands outside the low-risk allow rules
- commands with a timeout above the normal limit
- laptop commands that require human approval

The human must explicitly approve a confirmation prompt. The model cannot approve
its own request.

### DENY

Used for requests such as:

- unknown or unregistered tools
- paths outside the allowed workspace
- sensitive credential or secret paths
- destructive commands
- background or detached execution
- malformed or invalid tool arguments
- blank or invalid commands

Denied requests must not reach MCP execution.

## Important Invariant

```text
Model proposes.
Runtime validates.
Policy decides.
Human confirms when required.
MCP transports.
Tools execute only after authorization.
```

For detailed implementation and security limits, see:

```text
docs/reference/authority-boundary.mdx
docs/reference/current-limitations.mdx
src/odessy/policy.py
```
