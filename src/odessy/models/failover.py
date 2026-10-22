"""Failover: when the cloud provider is down, the turn moves to the next backend.

``ODESSY_CLOUD_FAILOVER`` lists backends to try, in order, after the selected cloud
provider: other cloud providers (``deepseek,qwen``) and, for an explicit cloud backend,
a local one (``lfm`` or ``ollama``) as the last resort. Each fallback provider uses its
own API key variable and preset; ``ODESSY_CLOUD_<NAME>_MODEL`` picks another model.

When a turn moves:

* **Only before any tool ran.** The first request of a turn (``propose``) may move.
  A provider that fails later in the turn ends that turn: the conversation so far is
  in its own wire format, and tools already ran under it. The next turn starts on a
  backend that is up.
* **Only when the provider is unavailable** (``CloudUnavailableError``: rate limits,
  overloads, server errors, timeouts, connection failures, after the adapter's own
  retries). A rejected key, a bad request, a refusal, or a spent budget fails the same
  way anywhere and is reported as it is.
* **Said out loud.** Every move is announced, naming where the request now goes, and
  recorded in the egress log.

A provider that failed is skipped for ``ODESSY_CLOUD_FAILOVER_COOLDOWN`` seconds
(60 by default), then tried first again. When every backend is cooling down, all are
tried in order rather than failing without a request.

Failover changes only which model proposes. Every proposal still goes through the
same validation, policy, and confirmation.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from odessy.authority.egress import egress_log
from odessy.models.cloud_model import (
    CLOUD_PROVIDERS,
    CloudModel,
    CloudModelConfigError,
    CloudUnavailableError,
    build_cloud_model,
)

FAILOVER_ENV = "ODESSY_CLOUD_FAILOVER"
COOLDOWN_ENV = "ODESSY_CLOUD_FAILOVER_COOLDOWN"
DEFAULT_COOLDOWN_SECONDS = 60.0
LOCAL_FALLBACKS = ("lfm", "ollama")
#: Settings that describe the selected provider only; a fallback uses its own preset.
_PRIMARY_ONLY = ("ODESSY_CLOUD_MODEL", "ODESSY_CLOUD_BASE_URL", "ODESSY_CLOUD_API_KEY")


@dataclass(frozen=True)
class FailoverConfig:
    backends: tuple[str, ...]
    cooldown: float = DEFAULT_COOLDOWN_SECONDS

    @property
    def cloud_backends(self) -> tuple[str, ...]:
        return tuple(name for name in self.backends if name in CLOUD_PROVIDERS)

    @classmethod
    def from_env(
        cls, primary: str, env: Mapping[str, str] | None = None, *, allow_local: bool = True
    ) -> FailoverConfig | None:
        """The failover list for ``primary``, or None when none is configured.

        Raises ``CloudModelConfigError`` for a name that is not a backend, a repeat,
        ``custom`` (its URL setting belongs to the selected provider), or a cost budget
        with another cloud provider in the list: prices are configured once, so a cost
        estimate across providers would be wrong. A token budget works across them.
        Local names are dropped when ``allow_local`` is false (auto routing, whose
        local roles already come first).
        """

        source = os.environ if env is None else env
        names = [name.strip().lower() for name in (source.get(FAILOVER_ENV) or "").split(",") if name.strip()]
        if not names:
            return None
        allowed = (*(name for name in CLOUD_PROVIDERS if name != "custom"), *LOCAL_FALLBACKS)
        seen = {primary}
        backends: list[str] = []
        for name in names:
            if name not in allowed:
                raise CloudModelConfigError(
                    f"{FAILOVER_ENV}: {name!r} cannot be a fallback; expected some of: {', '.join(allowed)}"
                )
            if name in seen:
                raise CloudModelConfigError(f"{FAILOVER_ENV}: {name!r} is listed twice or is the selected provider")
            seen.add(name)
            if name in LOCAL_FALLBACKS and not allow_local:
                continue
            backends.append(name)
        if not backends:
            return None
        from odessy.models.usage import BUDGET_COST_ENV
        if (source.get(BUDGET_COST_ENV) or "").strip() and any(name in CLOUD_PROVIDERS for name in backends):
            raise CloudModelConfigError(
                f"{BUDGET_COST_ENV} assumes one provider's prices; with other cloud providers in "
                f"{FAILOVER_ENV}, use ODESSY_CLOUD_BUDGET_TOKENS instead"
            )
        raw = (source.get(COOLDOWN_ENV) or "").strip()
        try:
            cooldown = float(raw) if raw else DEFAULT_COOLDOWN_SECONDS
        except ValueError:
            raise CloudModelConfigError(f"{COOLDOWN_ENV} must be a number of seconds") from None
        if not cooldown >= 0 or cooldown == float("inf"):
            raise CloudModelConfigError(f"{COOLDOWN_ENV} must be a finite number of seconds, zero or more")
        return cls(tuple(backends), cooldown)


def fallback_env(name: str, env: Mapping[str, str]) -> dict[str, str]:
    """``env`` for building fallback provider ``name``: its own preset and model."""

    result = {key: value for key, value in env.items() if key not in _PRIMARY_ONLY}
    model = (env.get(f"ODESSY_CLOUD_{name.upper()}_MODEL") or "").strip()
    if model:
        result["ODESSY_CLOUD_MODEL"] = model
    return result


def _describe(name: str, model: Any) -> str:
    return f"{name}/{getattr(model, 'model', type(model).__name__)}"


class FailoverModel:
    """A cloud adapter with fallbacks. Same ``ModelAdapter`` surface as ``CloudModel``.

    It has no ``begin_turn``, so the runtime passes conversation history to
    ``propose`` exactly as it does for a single cloud model.
    """

    uses_cloud = True

    def __init__(
        self,
        candidates: list[tuple[str, Any]],
        *,
        cooldown: float = DEFAULT_COOLDOWN_SECONDS,
        announce: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if len(candidates) < 2:
            raise ValueError("Failover needs the selected backend and at least one fallback")
        self.candidates = list(candidates)
        self.cooldown = cooldown
        self._announce = announce or (lambda message: print(message, file=sys.stderr, flush=True))
        self._clock = clock
        self._down_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._active = self.candidates[0]
        #: Set by the auto router before ``propose``; used when no context is passed.
        self.conversation_context: Any = ()

    # -- what the rest of Odessy reads ------------------------------------------

    @property
    def primary(self) -> Any:
        return self.candidates[0][1]

    @property
    def provider(self) -> str:
        return self._active[0]

    @property
    def model(self) -> str:
        return getattr(self._active[1], "model", type(self._active[1]).__name__)

    @property
    def active(self) -> Any:
        return self._active[1]

    @property
    def last_turn_diagnostics(self) -> Any:
        return getattr(self._active[1], "last_turn_diagnostics", None)

    def chain(self) -> str:
        return " -> ".join(_describe(name, model) for name, model in self.candidates)

    # -- ModelAdapter ----------------------------------------------------------

    def propose(self, user_text: str, *, context: Any = None) -> Any:
        if context is None and self.conversation_context:
            context = self.conversation_context
        failed: tuple[str, BaseException] | None = None
        order = self._order()
        if order[0][0] != self.candidates[0][0]:
            failed = (self.candidates[0][0], CloudUnavailableError('provider is in cooldown'))
        for name, model in order:
            if failed is not None:
                self._report_move(failed, name, model)
            self._active = (name, model)
            try:
                if context is None:
                    return model.propose(user_text)
                return model.propose(user_text, context=context)
            except CloudUnavailableError as error:
                self._mark_down(name)
                failed = (name, error)
        assert failed is not None
        raise failed[1]

    def respond_to_observation(self, user_text: str, observation: Any) -> Any:
        return self._continue("respond_to_observation", user_text, observation)

    def respond_to_observations(self, user_text: str, observations: Any) -> Any:
        return self._continue("respond_to_observations", user_text, observations)

    def finalize_observations(self, observations: Any) -> None:
        return self._continue("finalize_observations", observations)

    def request_stop(self) -> None:
        stop = getattr(self._active[1], "request_stop", None)
        if callable(stop):
            stop()

    # -- mechanics -------------------------------------------------------------

    def _continue(self, method: str, *args: Any) -> Any:
        name, model = self._active
        try:
            return getattr(model, method)(*args)
        except CloudUnavailableError:
            # Tools already ran under this provider's conversation; end the turn here,
            # and start the next one elsewhere.
            self._mark_down(name)
            raise

    def _order(self) -> list[tuple[str, Any]]:
        now = self._clock()
        with self._lock:
            up = [entry for entry in self.candidates if self._down_until.get(entry[0], 0.0) <= now]
        return up or list(self.candidates)

    def _mark_down(self, name: str) -> None:
        with self._lock:
            self._down_until[name] = self._clock() + self.cooldown

    def _report_move(self, failed: tuple[str, BaseException], name: str, model: Any) -> None:
        source, error = failed
        local = not isinstance(model, CloudModel)
        where = (
            "this machine's local model; nothing more leaves the machine"
            if local
            else f"{name}: your request and redacted tool output are sent there"
        )
        self._announce(f"[odessy] {source} is unavailable ({error}); this turn now uses {_describe(name, model)} ({where}).")
        log = egress_log()
        if log is not None:
            log.failover(from_backend=source, to_backend=name, to_local=local, error_type=type(error).__name__)


def build_cloud_model_with_failover(
    provider: str,
    env: Mapping[str, str] | None = None,
    *,
    local_factory: Callable[[str], Any] | None = None,
) -> CloudModel | FailoverModel:
    """The cloud model for ``provider``, wrapped with the configured fallbacks if any.

    Every fallback is built now, so a missing key is reported at startup, not in the
    middle of an outage. ``local_factory`` builds local fallbacks; without it they are
    ignored.
    """

    env = dict(os.environ) if env is None else dict(env)
    primary = build_cloud_model(provider, env)
    config = FailoverConfig.from_env(provider, env, allow_local=local_factory is not None)
    if config is None:
        return primary
    candidates: list[tuple[str, Any]] = [(provider, primary)]
    for name in config.backends:
        if name in CLOUD_PROVIDERS:
            try:
                candidates.append((name, build_cloud_model(name, fallback_env(name, env))))
            except CloudModelConfigError as error:
                raise CloudModelConfigError(f"{FAILOVER_ENV} fallback {name}: {error}") from error
        else:
            assert local_factory is not None
            candidates.append((name, local_factory(name)))
    return FailoverModel(candidates, cooldown=config.cooldown)
