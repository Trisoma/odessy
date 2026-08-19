from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from odessy.types import FinalResponse, ModelAction, ToolObservation, ToolRequest
from odessy.tool_contracts import (
    TOOL_REQUEST_SCHEMAS,
    render_tool_contract,
    render_tool_contracts,
    validate_tool_arguments,
)


@dataclass(frozen=True)
class ModelOutputError:
    raw_output: str
    validation_error: str
    tool_name: str | None = None
    tool_contract: str | None = None


@dataclass(frozen=True)
class OllamaModel:
    """Minimal Gemma/Ollama adapter for Odessy's existing ModelAdapter protocol."""

    model: str = "gemma3:1b"
    timeout: int = 120

    def propose(self, user_text: str) -> ModelAction:
        prompt = _proposal_prompt(user_text)
        response = self._run_ollama(prompt)
        try:
            action = _parse_model_action(response)
        except ValueError as error:
            model_error = _model_output_error(response, error)
            repair_prompt = _repair_prompt(user_text, model_error)
            _debug_model_repair(
                [
                    ("first Gemma proposal", response),
                    ("validation failure", model_error.validation_error),
                    ("repair prompt", repair_prompt),
                ]
            )
            repaired_response = self._run_ollama(repair_prompt)
            _debug_model_repair([("second Gemma proposal", repaired_response)])
            try:
                action = _parse_model_action(repaired_response)
            except ValueError as retry_error:
                raise ValueError(
                    f"Model repair failed after one retry: {retry_error}"
                ) from retry_error
            _debug_model_repair([("final parsed action type", type(action).__name__)])
            return action
        _debug_model_repair([("final parsed action type", type(action).__name__)])
        return action

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
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON model output: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ValueError("Model output must be a JSON object")
    if payload.get("type") == "tool":
        name = payload.get("name")
        arguments = payload.get("arguments", {})
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Malformed tool request: missing tool name")
        validate_tool_arguments(name, arguments)
        return ToolRequest(name=name, arguments=arguments)
    if isinstance(payload.get("text"), str):
        return FinalResponse(text=payload["text"])
    raise ValueError("Model output must be a final response or tool request")


def _model_output_error(raw_output: str, error: ValueError) -> ModelOutputError:
    tool_name = _extract_tool_name(raw_output)
    tool_contract = (
        render_tool_contract(tool_name)
        if tool_name in TOOL_REQUEST_SCHEMAS
        else render_tool_contracts()
    )
    return ModelOutputError(
        raw_output=raw_output,
        validation_error=str(error),
        tool_name=tool_name,
        tool_contract=tool_contract,
    )


def _extract_tool_name(raw_output: str) -> str | None:
    text = raw_output.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    return name if isinstance(name, str) and name.strip() else None


def _repair_prompt(user_text: str, error: ModelOutputError) -> str:
    return (
        "Your previous tool request was invalid.\n\n"
        "User request:\n"
        f"{user_text}\n\n"
        "Previous output:\n"
        f"{error.raw_output}\n\n"
        "Validation error:\n"
        f"{error.validation_error}\n\n"
        f"{error.tool_contract or render_tool_contracts()}\n\n"
        "Return ONLY a corrected JSON ToolRequest.\n"
        "Do not explain your answer.\n"
        "Use the required tool schema exactly."
    )


def _debug_model_repair(entries: list[tuple[str, str]]) -> None:
    if os.environ.get("ODESSY_DEBUG_MODEL_REPAIR") != "1":
        return
    for label, value in entries:
        print(f"[odessy:model-repair] {label}:\n{value}")
