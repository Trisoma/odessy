from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from odessy.types import (
    FinalResponse,
    ModelAction,
    ModelTurnDiagnostics,
    ToolObservation,
    ToolRequest,
)
from odessy.tool_contracts import (
    TOOL_REQUEST_SCHEMAS,
    build_openai_tools,
    render_tool_contract,
    render_tool_contracts,
    validate_tool_arguments,
)

OLLAMA_BASE_URL = os.environ.get("ODESSY_OLLAMA_URL", "http://127.0.0.1:11434")


class _OllamaToolsUnsupported(RuntimeError):
    """Raised when the selected model does not support native function calling."""


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
    temperature: float = 0.0

    @property
    def last_turn_diagnostics(self) -> ModelTurnDiagnostics:
        diagnostics = getattr(self, "_last_turn_diagnostics", None)
        if isinstance(diagnostics, ModelTurnDiagnostics):
            return diagnostics
        return ModelTurnDiagnostics()

    def propose(self, user_text: str) -> ModelAction:
        diagnostics = ModelTurnDiagnostics()
        object.__setattr__(self, "_last_turn_diagnostics", diagnostics)
        tool_name = _proposal_tool_name_for_user_text(user_text)
        system_prompt = _system_prompt(tool_name)
        fallback_prompt = _text_system_prompt(user_text, tool_name)
        tools = _select_tools(tool_name)
        try:
            action = self._chat_action(
                user_text,
                system_prompt,
                tools,
                fallback_prompt,
                diagnostics,
            )
        except ValueError as error:
            diagnostics.proposal_valid = False
            diagnostics.repair_triggered = True
            model_error = _model_output_error("", error, user_text, tool_name)
            repair_prompt = _repair_prompt(user_text, model_error)
            _debug_model_repair(
                [
                    ("first Gemma proposal", "native tool call rejected"),
                    ("validation failure", model_error.validation_error),
                    ("repair prompt", repair_prompt),
                ]
            )
            try:
                action = self._chat_action(
                    user_text,
                    repair_prompt,
                    tools,
                    repair_prompt,
                    diagnostics,
                    generation_field="repair_generation_ms",
                )
            except ValueError as retry_error:
                raise ValueError(
                    f"Model repair failed after one retry: {retry_error}"
                ) from retry_error
            _debug_model_repair([("second Gemma proposal", "repaired native tool call")])
        _debug_parsed_action(action)
        return action

    def _chat_action(
        self,
        user_text: str,
        system_prompt: str,
        tools: list[dict[str, Any]],
        fallback_prompt: str,
        diagnostics: ModelTurnDiagnostics,
        *,
        generation_field: str = "proposal_generation_ms",
    ) -> ModelAction:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
        try:
            started = time.perf_counter()
            response = self._chat(messages, tools=tools)
            _add_diagnostic_ms(diagnostics, generation_field, _elapsed_ms(started))
            _record_token_counts(diagnostics, response)
        except _OllamaToolsUnsupported:
            started = time.perf_counter()
            response = self._chat([{"role": "user", "content": fallback_prompt}], tools=None)
            _add_diagnostic_ms(diagnostics, generation_field, _elapsed_ms(started))
            _record_token_counts(diagnostics, response)
        parse_started = time.perf_counter()
        try:
            return _extract_model_action_from_chat(response, user_text)
        finally:
            if generation_field == "proposal_generation_ms":
                _add_diagnostic_ms(
                    diagnostics,
                    "proposal_parsing_ms",
                    _elapsed_ms(parse_started),
                )

    def _chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if tools:
            payload["tools"] = tools
        request = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = _read_error_body(error)
            if "does not support tools" in body:
                raise _OllamaToolsUnsupported(body) from error
            raise RuntimeError(
                f"Ollama API call failed ({error.code}): {body or error.reason}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Ollama API call failed: {error}") from error

    def respond_to_observation(
        self,
        user_text: str,
        observation: ToolObservation,
    ) -> ModelAction:
        prompt = (
            "You are Odessy. Return the tool output first, then a short interpretation.\n"
            "Do not answer with filler like 'Please wait' or 'Yes'.\n"
            f"User: {user_text}\n"
            f"Tool: {observation.request.name}\n"
            f"Arguments: {json.dumps(observation.request.arguments)}\n"
            f"Observation: {json.dumps(observation.result)}\n"
            "Final response:"
        )
        diagnostics = self.last_turn_diagnostics
        started = time.perf_counter()
        response = self._chat([{"role": "user", "content": prompt}])
        diagnostics.final_generation_ms = _elapsed_ms(started)
        _record_token_counts(diagnostics, response)
        object.__setattr__(self, "_last_turn_diagnostics", diagnostics)
        corrected = _corrected_tool_request(response, user_text)
        if corrected is not None:
            return corrected
        text = ((response.get("message") or {}).get("content") or "").strip()
        if text and not _looks_like_generic_final_response(text):
            return FinalResponse(text=text)
        return FinalResponse(text=_fallback_observation_response(observation))


def _corrected_tool_request(response: dict[str, Any], user_text: str) -> ToolRequest | None:
    """Return a ``ToolRequest`` when an observation was answered with a tool call.

    The continuation loop delivers a pre-execution rejection through
    ``respond_to_observation``, so a model that can propose a tool call must also
    be able to propose a *corrected* one.

    Only **native** tool calls are considered. A text answer keeps the historical
    summarisation path unchanged, and anything unparseable degrades to ``None``
    rather than raising into the loop.
    """

    message = response.get("message") or {}
    if not (message.get("tool_calls") or []):
        return None
    try:
        action = _extract_model_action_from_chat(response, user_text)
    except (ValueError, TypeError):
        return None
    return action if isinstance(action, ToolRequest) else None


def _system_prompt(tool_name: str | None) -> str:
    if tool_name is not None:
        intent_instruction = f"The user's request maps to {tool_name}. Call that tool."
    else:
        intent_instruction = "Call the single tool that best fulfills the user's request."
    return (
        "You are Odessy's tool-calling adapter.\n"
        f"{intent_instruction}\n"
        "Use the function tools provided in this request to carry out the user's intent.\n"
        "If the user only wants information and no tool applies, answer directly.\n"
        "Do not decide whether a tool is safe. The Odessy runtime will validate and authorize.\n"
    )


def _text_system_prompt(user_text: str, tool_name: str | None) -> str:
    """Text-mode prompt used when the model does not support native tool calling."""
    contract = render_tool_contract(tool_name) if tool_name is not None else render_tool_contracts()
    intent_instruction = (
        f"User intent maps to {tool_name}. MUST use {tool_name}."
        if tool_name is not None
        else "Choose the best tool for the user's request."
    )
    return (
        "Based on the user's request, generate exactly one JSON object.\n"
        f"{intent_instruction}\n"
        "For command requests, write the exact command in the command field.\n"
        "Leave optional fields omitted unless the user explicitly specifies them.\n"
        f"{contract}\n"
        "Do not add markdown, explanation, or extra text.\n"
        "Do not decide whether a tool is safe. The Odessy runtime will validate and authorize.\n"
        f"User: {user_text}\n"
        "JSON:"
    )


def _read_error_body(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", "replace")
    except Exception:
        return ""


def _select_tools(tool_name: str | None) -> list[dict[str, Any]]:
    if tool_name is not None:
        single = build_openai_tools([tool_name])
        if single:
            return single
    return build_openai_tools()


def _extract_model_action_from_chat(response: dict[str, Any], user_text: str) -> ModelAction:
    message = response.get("message") or {}
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        call = tool_calls[0]
        function = call.get("function") or {}
        name = function.get("name")
        arguments_raw = function.get("arguments", {})
        if isinstance(arguments_raw, str):
            try:
                arguments = json.loads(arguments_raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid tool arguments JSON: {error.msg}") from error
        else:
            arguments = arguments_raw
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Malformed tool call: missing function name")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        validate_tool_arguments(name, arguments)
        action = ToolRequest(name=name, arguments=arguments)
        _validate_tool_selection_for_user_intent(user_text, action)
        return action
    content = message.get("content") or ""
    if content.strip():
        return _parse_and_validate_model_action(content, user_text)
    raise ValueError("Ollama returned neither a tool call nor content")


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


#: Bare acknowledgements that carry no answer. The observation prompt tells the
#: model not to reply with these ("Do not answer with filler like 'Please wait'
#: or 'Yes'"), so they are matched explicitly rather than by shape.
_GENERIC_FINAL_RESPONSES = frozenset(
    {"yes", "no", "yeah", "yep", "ok", "okay", "sure", "done", "hello", "hi", "please wait"}
)


def _looks_like_generic_final_response(response: str) -> bool:
    """Whether a final answer is filler that should be replaced by a fallback.

    Filler is matched by an explicit vocabulary, **not** by length. A previous
    ``len(normalized) < 12`` heuristic classified any answer shorter than 12
    characters as generic, which silently discarded legitimate terse replies
    ("It is noon.", "12 files", "No matches."). For a voice agent, terse is the
    normal case, so length is not a usable signal here.
    """

    normalized = response.strip().lower().strip(" .!`")
    if not normalized:
        return True
    return normalized in _GENERIC_FINAL_RESPONSES


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
    intended_tool_name: str | None = None,
) -> ModelOutputError:
    tool_name = _extract_tool_name(raw_output) or intended_tool_name
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


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _add_diagnostic_ms(
    diagnostics: ModelTurnDiagnostics,
    field_name: str,
    value: float,
) -> None:
    current = getattr(diagnostics, field_name)
    setattr(diagnostics, field_name, value if current is None else current + value)


def _record_token_counts(
    diagnostics: ModelTurnDiagnostics,
    response: dict[str, Any],
) -> None:
    _add_tokens(diagnostics, "prompt_tokens", response.get("prompt_eval_count"))
    _add_tokens(diagnostics, "completion_tokens", response.get("eval_count"))


def _add_tokens(
    diagnostics: ModelTurnDiagnostics,
    field_name: str,
    value: Any,
) -> None:
    if not isinstance(value, int):
        return
    current = getattr(diagnostics, field_name)
    setattr(diagnostics, field_name, value if current is None else current + value)
