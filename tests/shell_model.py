"""Fake model for execution tests that do not measure shell startup time."""

from odessy.fake_model import FakeModel
from odessy.types import ModelAction, ToolRequest


class ShellModel(FakeModel):
    def propose(self, user_text: str) -> ModelAction:
        action = super().propose(user_text)
        if isinstance(action, ToolRequest) and action.name == "terminal.run":
            # Allow slow CI PowerShell startup without changing runtime defaults.
            return ToolRequest(action.name, {**action.arguments, "timeout": 30})
        return action
