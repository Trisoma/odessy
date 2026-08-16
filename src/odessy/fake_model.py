from __future__ import annotations

from odessy.types import FinalResponse, ModelAction, ToolObservation, ToolRequest


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
        if observation.request.name == "time.now":
            iso_time = observation.result["iso_time"]
            return FinalResponse(text=f"The current time is {iso_time}.")
        if observation.request.name == "terminal.run":
            result = observation.result
            command = result["command"]
            status = result["status"]
            exit_code = result["exit_code"]
            return FinalResponse(
                text=f"Command `{command}` finished with status {status} and exit code {exit_code}."
            )
        if observation.request.name == "calculator.eval":
            result = observation.result
            return FinalResponse(
                text=f"{result['expression']} = {result['result']}"
            )
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
