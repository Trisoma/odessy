from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from odessy.types import FinalResponse, ModelAction, ToolObservation, ToolRequest
from odessy.tool_contracts import render_tool_contracts, validate_tool_arguments


@dataclass(frozen=True)
class OllamaModel:
    """Minimal Gemma/Ollama adapter for Odessy's existing ModelAdapter protocol."""

    model: str = "gemma3:1b"
    timeout: int = 120

    def propose(self, user_text: str) -> ModelAction:
        prompt = _proposal_prompt(user_text)
        response = self._run_ollama(prompt)
        return _parse_model_action(response)

    def respond_to_observation(
        self,
        user_text: str,
        observation: ToolObservation,
    ) -> FinalResponse:
        prompt = (
            "You are Odessy. Answer the user briefly using this tool observation.\n"
            f"User: {user_text}\n"
            f"Tool: {observation.request.name}\n"
            f"Arguments: {json.dumps(observation.request.arguments)}\n"
            f"Observation: {json.dumps(observation.result)}\n"
            "Final response:"
        )
        return FinalResponse(text=self._run_ollama(prompt).strip())

    def _run_ollama(self, prompt: str) -> str:
        result = subprocess.run(
            ["ollama", "run", self.model, prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Ollama failed").strip())
        return result.stdout


def _proposal_prompt(user_text: str) -> str:
    return (
        "You are Odessy's local model adapter. Given the user's request, choose the tool request or final answer that best fulfills the user's intention.\n"
        "For normal answers: {\"type\":\"final\",\"text\":\"...\"}\n"
        "When the user asks to run a command on their laptop (for example, \"on my laptop\"), use laptop.terminal.run instead of terminal.run.\n"
        f"{render_tool_contracts()}\n"
        "Do not decide whether a tool is safe. The Odessy runtime will validate and authorize.\n"
        f"User: {user_text}\n"
        "JSON:"
    )


def _parse_model_action(raw_text: str) -> ModelAction:
    text = raw_text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        payload: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError:
        return FinalResponse(text=raw_text.strip())
    if payload.get("type") == "tool":
        name = payload.get("name")
        arguments = payload.get("arguments", {})
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Malformed tool request: missing tool name")
        validate_tool_arguments(name, arguments)
        return ToolRequest(name=name, arguments=arguments)
    if isinstance(payload.get("text"), str):
        return FinalResponse(text=payload["text"])
    return FinalResponse(text=raw_text.strip())
