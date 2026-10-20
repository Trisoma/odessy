from __future__ import annotations

from odessy.runtime.types import FinalResponse, ModelAction, ToolObservation, ToolRequest


class FakeModel:
    """Deterministic stand-in for Gemma during runtime development."""

    def propose(self, user_text: str) -> ModelAction:
        normalized = user_text.strip().lower()
        if normalized.startswith("run "):
            return _terminal_request(user_text.strip()[4:])
        if normalized.startswith("calculate "):
            return ToolRequest(
                name="calculator.eval",
                arguments={"expression": user_text.strip()[10:].strip()},
            )
        if normalized.startswith("read "):
            return ToolRequest(
                name="filesystem.read",
                arguments={"path": user_text.strip()[5:].strip()},
            )
        if normalized.startswith("list"):
            path = user_text.strip()[4:].strip() or "."
            return ToolRequest(name="filesystem.list", arguments={"path": path})
        if normalized.startswith("find "):
            return ToolRequest(
                name="project.find",
                arguments={"pattern": user_text.strip()[5:].strip(), "path": "."},
            )
        if normalized.startswith("grep "):
            return ToolRequest(
                name="project.grep",
                arguments={"pattern": user_text.strip()[5:].strip(), "path": "."},
            )
        if normalized == "git status":
            return ToolRequest(name="git.status", arguments={})
        if normalized.startswith("git diff"):
            mode = "cached" if "--cached" in normalized else "working"
            return ToolRequest(name="git.diff", arguments={"mode": mode})
        if normalized.startswith("git changed"):
            return ToolRequest(name="git.changed_files", arguments={"mode": "all"})
        if normalized.startswith("git log"):
            return ToolRequest(name="git.log", arguments={"limit": 20})
        if "time" in normalized or "date" in normalized:
            return ToolRequest(name="time.now", arguments={})
        return FinalResponse(
            text="I can demonstrate terminal.run with input like: run pwd"
        )

    def respond_to_observation(
        self,
        user_text: str,
        observation: ToolObservation,
    ) -> FinalResponse:
        if observation.result.get("status") == "error":
            return FinalResponse(
                text=f"{observation.request.name} failed: {observation.result.get('error', 'unknown error')}"
            )
        if observation.request.name == "time.now":
            iso_time = observation.result["iso_time"]
            return FinalResponse(text=f"The current time is {iso_time}.")
        if observation.request.name == "terminal.run":
            result = observation.result
            command = result["command"]
            status = result["status"]
            exit_code = result["exit_code"]
            # A timed-out command has no exit status of its own, so there is no number
            # to print. Saying "exit code None" would be worse than saying nothing.
            if exit_code is None:
                return FinalResponse(
                    text=f"Command `{command}` finished with status {status} (no exit code)."
                )
            return FinalResponse(
                text=f"Command `{command}` finished with status {status} and exit code {exit_code}."
            )
        if observation.request.name == "calculator.eval":
            result = observation.result
            return FinalResponse(
                text=f"{result['expression']} = {result['result']}"
            )
        if observation.request.name == "filesystem.read":
            result = observation.result
            return FinalResponse(text=f"Read {result['path']}:\n{result['content']}")
        if observation.request.name == "filesystem.list":
            entries = observation.result["entries"]
            names = ", ".join(entry["name"] for entry in entries)
            return FinalResponse(text=f"Directory entries: {names}")
        if observation.request.name in {"project.find", "project.grep"}:
            return FinalResponse(
                text=f"{observation.request.name} found {observation.result['count']} result(s)."
            )
        if observation.request.name.startswith("git."):
            return FinalResponse(text=f"{observation.request.name} returned a structured result.")
        return FinalResponse(text="I received a tool result, but I cannot summarize it yet.")


def _terminal_request(raw_command: str) -> ToolRequest:
    cwd = "."
    command = raw_command
    if raw_command.startswith("--cwd "):
        parts = raw_command.split(maxsplit=2)
        if len(parts) >= 3:
            cwd = parts[1]
            command = parts[2]
    return ToolRequest(
        name="terminal.run",
        arguments={
            "command": command,
            "cwd": cwd,
            "timeout": 10,
        },
    )
