"""LFM2.5-1.2B-Thinking model adapter over Ollama's HTTP API.

This adapter is intentionally thin. It reuses Odessy's shared proposal parsing,
validation, repair, and observation helpers from ``ollama_model`` so that the
model-specific code is limited to backend selection. LFM2.5 is a separate
backend, not a fork of the authority, policy, or validation logic.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from odessy.tools.tool_contracts import build_openai_tools
from odessy.models.ollama_model import (
    _OllamaStreamTimeout,
    _OllamaToolsUnsupported,
    _accumulate_stream,
    _corrected_tool_request,
    _debug_model_repair,
    _debug_parsed_action,
    _default_call_timeout,
    _default_num_ctx,
    _default_num_predict,
    _default_progress_timeout,
    _extract_model_action_from_chat,
    _fallback_observation_response,
    _follow_up_tools,
    _history_messages,
    _observation_messages,
    _remember_observation,
    _start_turn,
    _start_turn_with_context,
    _strip_thinking,
    _looks_like_generic_final_response,
    _model_output_error,
    _repair_prompt,
    _system_prompt,
    _text_system_prompt,
    PROGRESS_TIMEOUT_ENV,
    generation_options,
)
from odessy.runtime.types import FinalResponse, ModelAction, ModelTurnDiagnostics, ToolObservation

DEFAULT_LFM_ENDPOINT = os.environ.get("ODESSY_MODEL_ENDPOINT", "http://127.0.0.1:11434")
DEFAULT_LFM_MODEL = "lfm2.5-thinking:1.2b-q8_0"


def _read_error_body(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", "replace")
    except Exception:
        return ""


def normalize_ollama_chat_response(data: dict[str, Any]) -> dict[str, Any]:
    """Return the Ollama ``/api/chat`` response shape used by shared parsers."""
    message = data.get("message") or {}
    normalized = {
        "message": {
            "role": message.get("role", "assistant"),
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
        }
    }
    if "thinking" in message:
        normalized["message"]["thinking"] = message.get("thinking")
    for key in (
        "total_duration",
        "load_duration",
        "prompt_eval_duration",
        "prompt_eval_count",
        "eval_duration",
        "eval_count",
        "thinking",
        "thinking_token_count",
        "thinking_tokens",
    ):
        if key in data:
            normalized[key] = data[key]
    return normalized


@dataclass(frozen=True)
class LlamaCppLFMModel:
    """LFM2.5 adapter over Ollama's ``/api/chat``.

    The model proposes structured output and Odessy performs the same parsing,
    validation, repair, and authority checks as for Gemma. The model never
    becomes the authority layer.
    """

    model: str = DEFAULT_LFM_MODEL
    endpoint: str = DEFAULT_LFM_ENDPOINT
    #: Seconds of allowed silence between streamed chunks. Not a total budget.
    timeout: int = field(default_factory=_default_progress_timeout)
    temperature: float = 0.0
    think: bool | str | None = None
    #: Tokens one call may generate, including thinking tokens.
    num_predict: int = field(default_factory=_default_num_predict)
    #: Context window; prompt plus ``num_predict`` has to fit inside it.
    num_ctx: int = field(default_factory=_default_num_ctx)
    #: Stream wall-time budget checked between lines; includes silence, excludes
    #: connection/header waits, and can overrun during a blocking read.
    call_timeout: float = field(default_factory=_default_call_timeout)
    keep_alive: int | None = None
    conversation_context: tuple = ()

    @property
    def last_turn_diagnostics(self) -> ModelTurnDiagnostics:
        diagnostics = getattr(self, "_last_turn_diagnostics", None)
        if isinstance(diagnostics, ModelTurnDiagnostics):
            return diagnostics
        return ModelTurnDiagnostics()

    def propose(self, user_text: str, *, context: Any = None) -> ModelAction:
        diagnostics = ModelTurnDiagnostics()
        object.__setattr__(self, "_last_turn_diagnostics", diagnostics)
        _start_turn_with_context(self, context)
        try:
            action = self._chat_native_action(user_text, diagnostics)
        except ValueError as error:
            diagnostics.proposal_valid = False
            diagnostics.repair_triggered = True
            model_error = _model_output_error("", error, user_text)
            repair_prompt = _repair_prompt(user_text, model_error)
            _debug_model_repair(
                [
                    ("first LFM proposal", "invalid structured output"),
                    ("validation failure", model_error.validation_error),
                    ("repair prompt", repair_prompt),
                ]
            )
            try:
                action = self._chat_text_action(
                    user_text,
                    repair_prompt,
                    diagnostics,
                    generation_field="repair_generation_ms",
                )
            except ValueError as retry_error:
                raise ValueError(
                    f"Model repair failed after one retry: {retry_error}"
                ) from retry_error
            _debug_model_repair([("second LFM proposal", "repaired structured output")])
        _debug_parsed_action(action)
        return action

    def _chat_text_action(
        self,
        user_text: str,
        prompt: str,
        diagnostics: ModelTurnDiagnostics,
        *,
        generation_field: str = "proposal_generation_ms",
    ) -> ModelAction:
        started = time.perf_counter()
        _record_model_call_attempt(diagnostics)
        response = self._chat([{"role": "user", "content": prompt}])
        _add_diagnostic_ms(diagnostics, generation_field, _elapsed_ms(started))
        _record_ollama_response(diagnostics, response)
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

    def _chat_native_action(
        self,
        user_text: str,
        diagnostics: ModelTurnDiagnostics,
    ) -> ModelAction:
        messages = [
            {"role": "system", "content": _system_prompt()},
            *_history_messages(self),
            {"role": "user", "content": user_text},
        ]
        try:
            started = time.perf_counter()
            _record_model_call_attempt(diagnostics)
            response = self._chat(messages, tools=build_openai_tools())
            _add_diagnostic_ms(diagnostics, "proposal_generation_ms", _elapsed_ms(started))
            _record_ollama_response(diagnostics, response)
        except _OllamaToolsUnsupported:
            return self._chat_text_action(
                user_text,
                _text_system_prompt(user_text),
                diagnostics,
            )
        parse_started = time.perf_counter()
        try:
            return _extract_lfm_native_action_from_chat(response, user_text)
        finally:
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
        from odessy.models.generation_control import before_generation
        before_generation()
        if self.conversation_context:
            split = 1 if messages and messages[0].get("role") == "system" else 0
            messages = [*messages[:split], *self.conversation_context, *messages[split:]]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            # Inside ``options``, which is where Ollama reads it. A top-level
            # ``temperature`` is ignored, so this used to sample at Ollama's
            # default instead of at the intended 0.0.
            "options": generation_options(self.temperature, self.num_predict, self.num_ctx),
        }
        if self.think is not None:
            payload["think"] = self.think
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        if tools:
            payload["tools"] = tools
        request = urllib.request.Request(
            f"{self.endpoint.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return normalize_ollama_chat_response(
                    _accumulate_stream(response, self.call_timeout)
                )
        except urllib.error.HTTPError as error:
            body = _read_error_body(error)
            if "does not support tools" in body:
                raise _OllamaToolsUnsupported(body) from error
            raise RuntimeError(
                f"Ollama LFM API call failed ({error.code}): {body or error.reason}"
            ) from error
        except TimeoutError as error:
            raise _OllamaStreamTimeout(
                f"no chunk received within {self.timeout}s "
                f"({PROGRESS_TIMEOUT_ENV})"
            ) from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise _OllamaStreamTimeout(
                    f"no chunk received within {self.timeout}s "
                    f"({PROGRESS_TIMEOUT_ENV})"
                ) from error
            raise RuntimeError(f"Ollama LFM API call failed: {error}") from error

    def finalize_observations(self, observations) -> None:
        for observation in observations:
            _remember_observation(self, observation)

    def respond_to_observations(self, user_text, observations) -> ModelAction:
        for observation in observations[:-1]:
            _remember_observation(self, observation)
        return self.respond_to_observation(user_text, observations[-1])

    def respond_to_observation(
        self,
        user_text: str,
        observation: ToolObservation,
    ) -> ModelAction:
        diagnostics = self.last_turn_diagnostics
        started = time.perf_counter()
        _record_model_call_attempt(diagnostics)
        response = self._chat(
            _observation_messages(user_text, _remember_observation(self, observation)),
            tools=_follow_up_tools(),
        )
        diagnostics.final_generation_ms = _elapsed_ms(started)
        _record_ollama_response(diagnostics, response)
        object.__setattr__(self, "_last_turn_diagnostics", diagnostics)
        corrected = _corrected_tool_request(response, user_text)
        if corrected is not None:
            return corrected
        text = _strip_thinking((response.get("message") or {}).get("content") or "")
        if text and not _looks_like_generic_final_response(text):
            return FinalResponse(text=text)
        return FinalResponse(text=_fallback_observation_response(observation))


def _extract_lfm_native_action_from_chat(response: dict[str, Any], user_text: str) -> ModelAction:
    message = response.get("message") or {}
    if message.get("tool_calls"):
        return _extract_model_action_from_chat(response, user_text)
    content = _strip_thinking(message.get("content") or "")
    if not content:
        # Includes content that was only unfinished reasoning: there is no answer to
        # give, so the repair path asks again.
        raise ValueError("Ollama returned neither a tool call nor content")
    if content.startswith("{") or content.startswith("```"):
        return _extract_model_action_from_chat(response, user_text)
    return FinalResponse(text=content)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _add_diagnostic_ms(
    diagnostics: ModelTurnDiagnostics,
    field_name: str,
    value: float,
) -> None:
    current = getattr(diagnostics, field_name)
    setattr(diagnostics, field_name, value if current is None else current + value)


def _record_model_call_attempt(diagnostics: ModelTurnDiagnostics) -> None:
    diagnostics.model_call_count += 1


def _record_ollama_response(
    diagnostics: ModelTurnDiagnostics,
    response: dict[str, Any],
) -> None:
    _add_duration_ms(diagnostics, "model_total_duration_ms", response.get("total_duration"))
    _add_duration_ms(diagnostics, "model_load_duration_ms", response.get("load_duration"))
    _add_duration_ms(
        diagnostics,
        "prompt_eval_duration_ms",
        response.get("prompt_eval_duration"),
    )
    _add_duration_ms(diagnostics, "eval_duration_ms", response.get("eval_duration"))
    _add_tokens(diagnostics, "prompt_tokens", response.get("prompt_eval_count"))
    _add_tokens(diagnostics, "completion_tokens", response.get("eval_count"))
    _add_tokens(diagnostics, "prompt_eval_count", response.get("prompt_eval_count"))
    _add_tokens(diagnostics, "eval_count", response.get("eval_count"))

    message = response.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        diagnostics.response_chars += len(content)
    thinking = message.get("thinking", response.get("thinking"))
    if isinstance(thinking, str):
        diagnostics.thinking_exposed = True
        diagnostics.thinking_chars += len(thinking)

    thinking_tokens = response.get("thinking_token_count")
    if thinking_tokens is None:
        thinking_tokens = response.get("thinking_tokens")
    if thinking_tokens is None:
        thinking_tokens = message.get("thinking_token_count")
    _add_tokens(diagnostics, "thinking_token_count", thinking_tokens)

    if diagnostics.eval_duration_ms and diagnostics.eval_count is not None:
        diagnostics.generation_tokens_per_sec = diagnostics.eval_count / (
            diagnostics.eval_duration_ms / 1000.0
        )


def _add_duration_ms(
    diagnostics: ModelTurnDiagnostics,
    field_name: str,
    value: Any,
) -> None:
    if not isinstance(value, (int, float)):
        return
    value_ms = float(value) / 1_000_000.0
    current = getattr(diagnostics, field_name)
    setattr(
        diagnostics,
        field_name,
        value_ms if current is None else current + value_ms,
    )


def _add_tokens(
    diagnostics: ModelTurnDiagnostics,
    field_name: str,
    value: Any,
) -> None:
    if not isinstance(value, int):
        return
    current = getattr(diagnostics, field_name)
    setattr(diagnostics, field_name, value if current is None else current + value)
