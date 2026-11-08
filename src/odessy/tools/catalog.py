"""What kind of action each of Odessy's own tools is: one list for every authority check.

The policy, the approval snapshot, the session approvals and the collaboration
composition all ask the same questions of a tool name: does it spawn a process, is it
a read-only workspace tool, is it a collaboration tool, which arguments are paths,
which host does it run on. They used to keep a copy each. A new tool is added here,
in ``odessy.tools.tool_contracts`` (its arguments) and as a rule in
``odessy.authority.policy`` (its decision); a tool on the list without a rule is denied.

This module imports nothing from Odessy, so any layer can use it.
"""

from __future__ import annotations

#: Tools whose action spawns a process, and so have a sandbox mode.
PROCESS_TOOLS = frozenset({"terminal.run", "laptop.terminal.run"})

#: Tools that only read the workspace.
READ_ONLY_TOOLS = frozenset({
    "filesystem.read",
    "filesystem.list",
    "project.find",
    "project.grep",
    "git.status",
    "git.diff",
    "git.changed_files",
    "git.log",
    "git.show",
})

#: Tools served by the collaboration server.
COLLABORATION_TOOLS = frozenset({
    "model.consult", "agent.delegate", "agent.job_status", "agent.cancel",
    "agent.run", "agent.run_continue", "agent.run_status", "agent.run_cancel",
})

#: Collaboration tools that look at or stop work already authorized.
COLLABORATION_INSPECT_TOOLS = frozenset({"agent.job_status", "agent.cancel", "agent.run_status", "agent.run_cancel"})

#: Collaboration tools that start or hand over work, and so need a person's yes.
GRANTED_TOOLS = frozenset({"agent.delegate", "agent.run", "agent.run_continue"})

#: Every tool of Odessy's own the default policy knows.
BUILTIN_TOOLS = frozenset({
    "time.now",
    "user.ask",
    "calculator.eval",
    "filesystem.write",
    "log.append",
    *PROCESS_TOOLS,
    *READ_ONLY_TOOLS,
    *COLLABORATION_TOOLS,
})

#: Argument names that hold a path on the machine that runs the tool.
PATH_ARGUMENTS = frozenset({"path", "file", "cwd"})


def target_of(tool: str) -> str:
    """The host a tool runs on: the paired laptop, or this machine."""

    return "laptop" if tool.startswith("laptop.") else "local"


__all__ = [
    "BUILTIN_TOOLS",
    "COLLABORATION_INSPECT_TOOLS",
    "COLLABORATION_TOOLS",
    "GRANTED_TOOLS",
    "PATH_ARGUMENTS",
    "PROCESS_TOOLS",
    "READ_ONLY_TOOLS",
    "target_of",
]
