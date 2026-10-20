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
- safe read-only commands, each as a single command: listing (`pwd`, `ls`, `dir`),
  locating programs (`which`, `where`), `echo` without variables, `--version` of
  well-known programs, read-only git (`status`, `log`, `show`, `diff`, listing
  `branch`), and printing files (`cat`, `type`, `Get-Content`, `head`, `tail`, `wc`)
  that are all inside the workspace and not credential files
- `log.append`, whose destination is fixed inside the internal Odessy log area

### CONFIRM

Used for operations that may modify state or exceed the normal execution scope,
including:

- `filesystem.write`
- write-like commands
- installer commands (`pip install`, `npm install`, `yarn add`, ...), rated **high**
- non-read-only Git commands
- commands outside the low-risk allow rules
- commands with a timeout above the normal limit
- laptop commands that require human approval

The human must explicitly approve a confirmation prompt. The model cannot approve
its own request.

The terminal prompt (`odessy.runtime.confirmation`) keeps confirmations few enough
to be read:

- **High risk** (`risk: high`: installers, destructive third-party tools, a wider
  sandbox) is marked `!! HIGH RISK` and must be answered by typing `yes`.
- **Other confirmations** take `y`. For a command or a third-party tool, `a` allows it
  again without asking until Odessy exits: the exact same command (same arguments
  and directory), or every call to that one third-party tool. This is held in memory
  only, never written to a file, and never offered for high-risk calls or file writes.
- **`filesystem.write`** shows a diff against the current file (or the new file's
  lines), cut at 40 lines.
- Model-written text in the prompt is shown with control characters escaped, so an
  escape sequence cannot hide part of it.

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
