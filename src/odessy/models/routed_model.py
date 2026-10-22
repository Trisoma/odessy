"""Conservative opt-in routing. Selection never authorizes or executes tools."""
from __future__ import annotations

import copy
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, fields
from typing import Any, Callable, Mapping

from odessy.models.generation_control import generation_guard
from odessy.runtime.types import FinalResponse, ModelTurnDiagnostics

POLICY_VERSION = "conservative-v1"
DEFAULT_CAPABLE_MODEL = "lfm2.5-thinking:1.2b-q8_0"


@dataclass(frozen=True)
class RouteDecision:
    role: str
    reason: str


def select_route(text: str, conversation_context=()) -> RouteDecision:
    if conversation_context:
        return RouteDecision("capable", "conversation_context")
    if re.fullmatch(r"(?:hello|hi|hey|thanks|thank you)[.!?,\s]*", text.strip(), re.IGNORECASE):
        return RouteDecision("fast", "greeting")
    if re.fullmatch(r"(?:rewrite|rephrase|summarize|translate to [\w -]+):\s*\S[\s\S]*", text.strip(), re.IGNORECASE):
        return RouteDecision("fast", "explicit_text_transform")
    return RouteDecision("capable", "uncertain_or_tool_request")


@dataclass(frozen=True)
class RoutingConfig:
    fast_model: str = "gemma3:1b"
    capable_backend: str = "lfm"
    capable_model: str = DEFAULT_CAPABLE_MODEL
    cloud_fallback: bool = False
    cloud_backend: str | None = None

    def __post_init__(self):
        from odessy.models.cloud_model import CLOUD_PROVIDERS
        if self.capable_backend not in {"lfm", "ollama", "llama_cpp"}:
            raise ValueError("ODESSY_AUTO_CAPABLE_BACKEND must be lfm, ollama, or llama_cpp")
        if not self.fast_model.strip() or not self.capable_model.strip():
            raise ValueError("ODESSY_AUTO model names must not be empty")
        if self.cloud_fallback and self.cloud_backend not in CLOUD_PROVIDERS:
            raise ValueError("ODESSY_AUTO_CLOUD_BACKEND must name a cloud backend when fallback is on")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        flag = env.get("ODESSY_AUTO_CLOUD_FALLBACK", "off").lower()
        if flag not in {"on", "off"}:
            raise ValueError("ODESSY_AUTO_CLOUD_FALLBACK must be on or off")
        return cls(env.get("ODESSY_AUTO_FAST_MODEL", "gemma3:1b"),
                   env.get("ODESSY_AUTO_CAPABLE_BACKEND", "lfm"),
                   env.get("ODESSY_AUTO_CAPABLE_MODEL", DEFAULT_CAPABLE_MODEL),
                   flag == "on", env.get("ODESSY_AUTO_CLOUD_BACKEND") or None)


class _GenerationStopped(RuntimeError):
    pass


class RoutedModel:
    model = "auto"
    background_generation = True

    def __init__(self, config: RoutingConfig, *, fast_factory=None, capable_factory=None,
                 cloud_factory=None, announce: Callable[[str], None] | None = None):
        self.config = config
        self.uses_cloud = config.cloud_fallback
        self._factories = {"fast": fast_factory, "capable": capable_factory, "cloud": cloud_factory}
        self._children: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._announce = announce or (lambda message: print(message, file=sys.stderr, flush=True))
        self._context = ()
        self._stop = None
        self._deadline = None
        self._max_calls = 4
        self._prepared = False
        self._local_stop = threading.Event()
        self._reset()

    def _reset(self):
        self._role = None
        self._calls = 0
        self._stages = []
        self._reason = ""
        self._cancelled = False

    def begin_turn(self, *, max_model_calls=4, stop_event=None, deadline=None, conversation_context=()):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Cannot start a turn while model generation is active")
        try:
            self._reset()
            self._local_stop = threading.Event()
            self._max_calls = max_model_calls
            self._stop, self._deadline = stop_event, deadline
            from odessy.authority.redaction import redact_text
            self._context = tuple({"role": m["role"], "content": redact_text(m["content"])}
                                  for m in conversation_context
                                  if isinstance(m, dict) and m.get("role") in {"user", "assistant"}
                                  and isinstance(m.get("content"), str))
            self._prepared = True
        finally:
            self._lock.release()

    @property
    def routing_config(self):
        return {"policy_version": POLICY_VERSION, **asdict(self.config), "local_keep_alive": 0}

    @property
    def last_turn_diagnostics(self):
        total = ModelTurnDiagnostics()
        for stage in self._stages:
            diagnostic = stage.get("diagnostics", {})
            for field in fields(total):
                name = field.name
                value = diagnostic.get(name)
                if name in {"routing", "model_call_count", "generation_tokens_per_sec"} or value is None:
                    continue
                if isinstance(value, bool):
                    setattr(total, name, getattr(total, name) and value if name == "proposal_valid" else getattr(total, name) or value)
                elif isinstance(value, (int, float)):
                    setattr(total, name, (getattr(total, name) or 0) + value)
        total.model_call_count = self._calls
        total.routing = {"policy_version": POLICY_VERSION, "selected_role": self._role,
                         "reason": self._reason, "cancelled": self._cancelled,
                         "stages": copy.deepcopy(self._stages)}
        return total

    def _child(self, role):
        if role not in self._children:
            factory = self._factories[role]
            if factory is not None:
                child = factory()
            elif role == "cloud":
                from odessy.models.failover import build_cloud_model_with_failover
                child = build_cloud_model_with_failover(self.config.cloud_backend)
            else:
                from odessy.models.ollama_model import OllamaModel
                from odessy.models.lfm_model import LlamaCppLFMModel, DEFAULT_LFM_ENDPOINT
                endpoint = os.environ.get("ODESSY_MODEL_ENDPOINT", DEFAULT_LFM_ENDPOINT)
                if role == "fast":
                    child = OllamaModel(model=self.config.fast_model, text_only=True, num_predict=256, keep_alive=0, endpoint=endpoint)
                elif self.config.capable_backend == "ollama":
                    child = OllamaModel(model=self.config.capable_model, keep_alive=0, endpoint=endpoint)
                else:
                    child = LlamaCppLFMModel(model=self.config.capable_model, keep_alive=0, endpoint=endpoint)
            self._children[role] = child
        return self._children[role]

    def request_stop(self):
        self._local_stop.set()

    def _check(self):
        if self._local_stop.is_set() or (self._stop is not None and self._stop.is_set()):
            self._cancelled = True
            raise _GenerationStopped("Stopped. No further model calls or tools were dispatched.")
        if self._deadline is not None and self._deadline.expired:
            raise _GenerationStopped("Model deadline exhausted. No further tools were dispatched.")
        if self._calls >= self._max_calls:
            raise _GenerationStopped("Model call budget exhausted. No further tools were dispatched.")

    def _invoke(self, role, method, *args):
        self._check()
        child = self._child(role)
        self._role = role
        if method == "propose":
            # Context is supplied by the caller; the router owns no session history.
            object.__setattr__(child, "conversation_context", self._context)
            stage = {"role": role, "backend": self.config.cloud_backend if role == "cloud" else ("ollama" if role == "fast" else self.config.capable_backend),
                     "model": getattr(child, "model", type(child).__name__), "calls": 0}
            self._stages.append(stage)
            self._announce(
                f"[odessy] model: {stage['model']} "
                f"(backend={stage['backend']}, role={role}, reason={self._reason})"
            )
        else:
            stage = self._stages[-1]
        started = time.perf_counter()
        calls_before = self._calls
        guarded = False
        def guard():
            nonlocal guarded
            guarded = True
            self._check()
            self._calls += 1
            stage["calls"] += 1
        token = generation_guard.set(guard)
        try:
            result = getattr(child, method)(*args)
            if self._local_stop.is_set() or (self._stop is not None and self._stop.is_set()):
                self._cancelled = True
                raise _GenerationStopped("Stopped. Generated output was discarded.")
            if self._deadline is not None and self._deadline.expired:
                raise _GenerationStopped("Model deadline exhausted. Generated output was discarded.")
            return result
        finally:
            generation_guard.reset(token)
            if self._calls == calls_before and not guarded:
                self._calls += 1  # Scripted/custom adapters without transport hooks.
                stage["calls"] += 1
            stage["generation_ms"] = stage.get("generation_ms", 0) + (time.perf_counter()-started)*1000
            diagnostic = getattr(child, "last_turn_diagnostics", None)
            if isinstance(diagnostic, ModelTurnDiagnostics):
                stage["diagnostics"] = asdict(diagnostic)

    def propose(self, user_text):
        with self._lock:
            if not self._prepared:
                self.begin_turn()
            self._prepared = False
            decision = select_route(user_text, self._context)
            self._reason = decision.reason
            role = decision.role
            try:
                if role == "fast":
                    try:
                        result = self._invoke("fast", "propose", user_text)
                        if not isinstance(result, FinalResponse) or not result.text.strip():
                            raise ValueError("Fast adapter did not produce nonempty text")
                        return result
                    except (PermissionError, _GenerationStopped):
                        raise
                    except (ValueError, RuntimeError, OSError):
                        self._reason = "fast_failed"
                try:
                    return self._invoke("capable", "propose", user_text)
                except (PermissionError, _GenerationStopped):
                    raise
                except (ValueError, RuntimeError, OSError):
                    if not self.uses_cloud:
                        raise
                    self._check()
                    self._reason = "capable_failed"
                    self._announce(f"[odessy] auto escalation to {self.config.cloud_backend}: request and redacted tool output may leave this machine.")
                    return self._invoke("cloud", "propose", user_text)
            except _GenerationStopped as exc:
                return FinalResponse(str(exc))

    def _continue(self, method, *args):
        with self._lock:
            try:
                return self._invoke(self._role, method, *args)
            except _GenerationStopped as exc:
                return FinalResponse(str(exc))

    def respond_to_observation(self, user_text, observation):
        return self._continue("respond_to_observation", user_text, observation)

    def respond_to_observations(self, user_text, observations):
        return self._continue("respond_to_observations", user_text, observations)

    def finalize_observations(self, observations):
        if self._role is not None:
            finalize = getattr(self._children[self._role], "finalize_observations", None)
            if finalize:
                finalize(observations)


def model_uses_cloud(model) -> bool:
    from odessy.models.cloud_model import CloudModel
    return isinstance(model, CloudModel) or getattr(model, "uses_cloud", False) is True
