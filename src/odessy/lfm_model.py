"""LFM2.5-1.2B-Thinking model adapter over Ollama's HTTP API.

This adapter is intentionally thin. It reuses Odessy's shared proposal parsing,
validation, repair, and observation helpers from ``ollama_model`` so that the
model-specific code is limited to backend selection. LFM2.5 is a separate
backend, not a fork of the authority, policy, or validation logic.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from odessy.ollama_model import (
    _OllamaToolsUnsupported,
    _debug_model_repair,
    _debug_parsed_action,
    _extract_model_action_from_chat,
    _fallback_observation_response,
    _looks_like_generic_final_response,
    _model_output_error,
    _proposal_tool_name_for_user_text,
    _repair_prompt,
    _select_tools,
    _system_prompt,
    _text_system_prompt,
)
from odessy.types import FinalResponse, ModelAction, ToolObservation

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
    return {
        "message": {
            "role": message.get("role", "assistant"),
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
        }
    }


@dataclass(frozen=True)
class LlamaCppLFMModel:
    """LFM2.5 adapter over Ollama's ``/api/chat``.

    The model proposes structured output and Odessy performs the same parsing,
    validation, repair, and authority checks as for Gemma. The model never
    becomes the authority layer.
    """

    model: str = DEFAULT_LFM_MODEL
    endpoint: str = DEFAULT_LFM_ENDPOINT
    timeout: int = 120
    temperature: float = 0.0

    def propose(self, user_text: str) -> ModelAction:
        tool_name = _proposal_tool_name_for_user_text(user_text)
        prompt = _text_system_prompt(user_text, tool_name)
        try:
            action = self._chat_native_action(user_text, tool_name)
        except ValueError as error:
            model_error = _model_output_error("", error, user_text, tool_name)
            repair_prompt = _repair_prompt(user_text, model_error)
            _debug_model_repair(
                [
                    ("first LFM proposal", "invalid structured output"),
                    ("validation failure", model_error.validation_error),
                    ("repair prompt", repair_prompt),
                ]
            )
            try:
                action = self._chat_text_action(user_text, repair_prompt)
            except ValueError as retry_error:
                raise ValueError(
                    f"Model repair failed after one retry: {retry_error}"
                ) from retry_error
            _debug_model_repair([("second LFM proposal", "repaired structured output")])
        _debug_parsed_action(action)
        return action

    def _chat_text_action(self, user_text: str, prompt: str) -> ModelAction:
        response = self._chat([{"role": "user", "content": prompt}])
        return _extract_model_action_from_chat(response, user_text)

    def _chat_native_action(self, user_text: str, tool_name: str | None) -> ModelAction:
        messages = [
            {"role": "system", "content": _system_prompt(tool_name)},
            {"role": "user", "content": user_text},
        ]
        try:
            response = self._chat(messages, tools=_select_tools(tool_name))
        except _OllamaToolsUnsupported:
            return self._chat_text_action(user_text, _text_system_prompt(user_text, tool_name))
        return _extract_lfm_native_action_from_chat(response, user_text)

    def _chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": self.temperature,
        }
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
                return normalize_ollama_chat_response(json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as error:
            body = _read_error_body(error)
            if "does not support tools" in body:
                raise _OllamaToolsUnsupported(body) from error
            raise RuntimeError(
                f"Ollama LFM API call failed ({error.code}): {body or error.reason}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Ollama LFM API call failed: {error}") from error

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
        response = self._chat([{"role": "user", "content": prompt}])
        text = ((response.get("message") or {}).get("content") or "").strip()
        if text and not _looks_like_generic_final_response(text):
            return FinalResponse(text=text)
        return FinalResponse(text=_fallback_observation_response(observation))


def _extract_lfm_native_action_from_chat(response: dict[str, Any], user_text: str) -> ModelAction:
    message = response.get("message") or {}
    if message.get("tool_calls"):
        return _extract_model_action_from_chat(response, user_text)
    content = (message.get("content") or "").strip()
    if not content:
        raise ValueError("Ollama returned neither a tool call nor content")
    if content.startswith("{") or content.startswith("```"):
        return _extract_model_action_from_chat(response, user_text)
    return FinalResponse(text=content)
