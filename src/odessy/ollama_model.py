from __future__ import annotations

import json
import os
import re
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
            action = _parse_and_validate_model_action(response, user_text)
        except ValueError as error:
            model_error = _model_output_error(response, error, user_text)
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
                action = _parse_and_validate_model_action(repaired_response, user_text)
            except ValueError as retry_error:
                raise ValueError(
                    f"Model repair failed after one retry: {retry_error}"
                ) from retry_error
            _debug_parsed_action(action)
            return action
        _debug_parsed_action(action)
        return action

    def respond_to_observation(
        self,
        user_text: str,
        observation: ToolObservation,
    ) -> FinalResponse:
        prompt = (
            "You are Odessy. Return the tool output first, then a short interpretation.\n"
            "Do not answer with filler like 'Please wait' or 'Yes'.\n"
            f"User: {user_text}\n"
            f"Tool: {observation.request.name}\n"
            f"Arguments: {json.dumps(observation.request.arguments)}\n"
            f"Observation: {json.dumps(observation.result)}\n"
            "Final response:"
        )
        response = self._run_ollama(prompt).strip()
        if response and not _looks_like_generic_final_response(response):
            return FinalResponse(text=response)
        return FinalResponse(text=_fallback_observation_response(observation))

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
    tool_name = _proposal_tool_name_for_user_text(user_text)
    contracts = render_tool_contract(tool_name) if tool_name is not None else render_tool_contracts()
    intent_instruction = (
        f"User intent maps to {tool_name}. MUST use {tool_name}."
        if tool_name is not None
        else "Choose the best JSON shape for the user's request."
    )
    return (
        "Based on user's request, generate a JSON that include relevant linux command.\n"
        "Output exactly one JSON object.\n"
        f"{intent_instruction}\n"
        "For command requests, write the exact commandline command in the command field.\n"
        "For normal answers: {\"type\":\"final\",\"text\":\"...\"}\n"
        "Leave `cwd` untouched. Leave optional fields untouched unless the user explicitly says them.\n"
        f"{contracts}\n"
        "Do not add markdown, explanation, or extra text.\n"
        "Do not decide whether a tool is safe. The Odessy runtime will validate and authorize.\n"
        f"User: {user_text}\n"
        "JSON:"
    )


def _proposal_tool_name_for_user_text(user_text: str) -> str | None:
    normalized = user_text.strip().lower()
    if _is_laptop_command_intent(normalized):
        return "laptop.terminal.run"
    if normalized.startswith("run ") or " run " in normalized:
        return "terminal.run"
    if "git status" in normalized:
        return "git.status"
    if "git diff" in normalized:
        return "git.diff"
    if "git changed" in normalized or "changed files" in normalized:
        return "git.changed_files"
    if "git log" in normalized:
        return "git.log"
    if normalized.startswith("read ") or "read file" in normalized:
        return "filesystem.read"
    if normalized.startswith("list") or ("list" in normalized and "file" in normalized):
        return "filesystem.list"
    if normalized.startswith("find ") or "find file" in normalized:
        return "project.find"
    if normalized.startswith("grep ") or "search text" in normalized:
        return "project.grep"
    if normalized.startswith("calculate ") or "calculate" in normalized:
        return "calculator.eval"
    if "time" in normalized or "date" in normalized:
        return "time.now"
    return None


def _fallback_observation_response(observation: ToolObservation) -> str:
    name = observation.request.name
    result = observation.result
    if name == "time.now" and isinstance(result.get("iso_time"), str):
        return f"The current time is {result['iso_time']}."
    if name == "terminal.run":
        command = result.get("command", observation.request.arguments.get("command", "the command"))
        status = result.get("status", "ok")
        exit_code = result.get("exit_code", 0)
        return f"Command `{command}` finished with status {status} and exit code {exit_code}."
    if name == "calculator.eval":
        expression = result.get("expression", observation.request.arguments.get("expression", "the expression"))
        return f"{expression} = {result.get('result', 'unknown')}"
    if name == "filesystem.read":
        path = result.get("path", observation.request.arguments.get("path", "the file"))
        content = result.get("content", "")
        return f"Read {path}:\n{content}"
    if name == "filesystem.list":
        entries = result.get("entries", [])
        names = ", ".join(
            entry.get("name", "")
            for entry in entries
            if isinstance(entry, dict) and entry.get("name")
        )
        return f"Directory entries: {names}" if names else "Directory entries: (none)"
    if name in {"project.find", "project.grep"}:
        return f"{name} found {result.get('count', 0)} result(s)."
    if name.startswith("git."):
        return f"{name} returned a structured result."
    return "I received a tool result, but I cannot summarize it yet."


def _looks_like_generic_final_response(response: str) -> bool:
    normalized = response.strip().lower().strip(" .!`")
    if not normalized:
        return True
    if normalized in {"yes", "please wait", "ok", "okay", "done"}:
        return True
    return len(normalized) < 12


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


def _parse_and_validate_model_action(raw_text: str, user_text: str) -> ModelAction:
    action = _parse_model_action(raw_text)
    _validate_tool_selection_for_user_intent(user_text, action)
    return action


def _validate_tool_selection_for_user_intent(user_text: str, action: ModelAction) -> None:
    if (
        isinstance(action, ToolRequest)
        and action.name != "laptop.terminal.run"
        and _is_laptop_command_intent(user_text)
    ):
        raise ValueError(
            f"Laptop command requests must use laptop.terminal.run, not {action.name}"
        )


def _is_laptop_command_intent(user_text: str) -> bool:
    normalized = user_text.lower()
    return (
        "on my laptop" in normalized
        or "on the laptop" in normalized
        or "my laptop" in normalized
    )


def _model_output_error(
    raw_output: str,
    error: ValueError,
    user_text: str,
) -> ModelOutputError:
    tool_name = _extract_tool_name(raw_output)
    if (
        tool_name == "terminal.run"
        and _is_laptop_command_intent(user_text)
        and "Laptop command requests" in str(error)
    ):
        tool_name = "laptop.terminal.run"
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
        match = re.search(r'"name"\s*:\s*"([^"]+)"', raw_output)
        return match.group(1) if match else None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    return name if isinstance(name, str) and name.strip() else None


def _repair_prompt(user_text: str, error: ModelOutputError) -> str:
    return (
        "Your previous JSON output was invalid.\n\n"
        "User request:\n"
        f"{user_text}\n\n"
        "Previous output:\n"
        f"{error.raw_output}\n\n"
        "Validation error:\n"
        f"{error.validation_error}\n\n"
        f"{error.tool_contract or render_tool_contracts()}\n\n"
        "Return ONLY corrected JSON.\n"
        "Do not explain your answer.\n"
        "Use the required tool schema exactly."
    )


def _debug_model_repair(entries: list[tuple[str, str]]) -> None:
    if os.environ.get("ODESSY_DEBUG_MODEL_REPAIR") != "1":
        return
    for label, value in entries:
        print(f"[odessy:model-repair] {label}:\n{value}")


def _debug_parsed_action(action: ModelAction) -> None:
    if os.environ.get("ODESSY_DEBUG_MODEL_REPAIR") != "1":
        return
    if isinstance(action, ToolRequest):
        payload = json.dumps({"name": action.name, "arguments": action.arguments}, ensure_ascii=True)
        print(f"[odessy:model-repair] parsed tool request:\n{payload}")
    else:
        print(f"[odessy:model-repair] parsed final response:\n{action.text}")
