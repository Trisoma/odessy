"""Engine selection: one validated decision, made when the session is created.

Rules enforced here, and tested in ``tests/test_runtime_backends.py``:

- ``legacy`` is the default; an absent, empty or whitespace-only selection means legacy.
- An unknown engine name is malformed configuration (:class:`RuntimeSelectionError`).
- A known engine that has no registered backend is unavailable
  (:class:`RuntimeUnavailable`). It is **never** replaced by the legacy engine.
- A backend is built once per session. Nothing swaps an engine afterwards.

The registry is a plain dict of builders, not a plugin framework: prompt 03 registers the
Harness builder here when that engine exists in the build.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import (
    DEFAULT_ENGINE,
    ENGINES,
    EngineName,
    RuntimeSelectionError,
    RuntimeSession,
    RuntimeUnavailable,
)

BackendBuilder = Callable[[Any], RuntimeSession]


def _legacy_builder(session: Any) -> RuntimeSession:
    from .legacy import LegacyRuntime

    return LegacyRuntime(session)


#: Registered engines. A name in ``ENGINES`` that is absent here is *known but
#: unavailable* — the distinction matters, because only one of the two is a typo.
_BACKENDS: dict[str, BackendBuilder] = {"legacy": _legacy_builder}


def register_backend(engine: str, builder: BackendBuilder) -> None:
    """Register an engine builder. Re-registering the same engine is refused."""
    if engine not in ENGINES:
        raise RuntimeSelectionError(f"Unknown runtime engine: {engine!r}")
    if not callable(builder):
        raise TypeError("a runtime backend builder must be callable")
    if engine in _BACKENDS:
        raise RuntimeSelectionError(f"Runtime engine {engine!r} is already registered")
    _BACKENDS[engine] = builder


def available_engines() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))


def normalize_engine(value: Any) -> str:
    """Return a known engine name, or raise :class:`RuntimeSelectionError`."""
    if value is None:
        return DEFAULT_ENGINE
    if not isinstance(value, str):
        raise RuntimeSelectionError("Runtime engine must be a string")
    name = value.strip().lower()
    if not name:
        return DEFAULT_ENGINE
    if name not in ENGINES:
        raise RuntimeSelectionError(
            f"Unknown runtime engine {value!r}; expected one of {', '.join(ENGINES)}"
        )
    return name


@dataclass(frozen=True)
class RuntimeSelection:
    """The resolved engine and why it was chosen. Immutable on purpose.

    Validated on construction, so a preconstructed selection cannot smuggle an unknown
    engine past :func:`resolve_selection` and be reported as merely unavailable.
    """

    engine: str
    source: str  # "default" | "explicit"

    def __post_init__(self) -> None:
        object.__setattr__(self, "engine", normalize_engine(self.engine))
        if self.source not in ("default", "explicit"):
            raise RuntimeSelectionError(
                f"Runtime selection source must be 'default' or 'explicit', not {self.source!r}"
            )

    def public(self) -> dict[str, str]:
        return {"engine": self.engine, "source": self.source}


def resolve_selection(value: Any = None, *, environ: Mapping[str, str] | None = None) -> RuntimeSelection:
    """Resolve an explicit selection, else ``ODESSY_RUNTIME_ENGINE``, else legacy.

    ``value`` may be a :class:`RuntimeSelection`, an engine string, or a mapping with an
    ``engine`` key. Passing both a value and a conflicting environment setting is not an
    error: the explicit value wins and the environment is ignored.
    """
    if isinstance(value, RuntimeSelection):
        # Already validated in __post_init__; return a value with a normalized engine.
        return RuntimeSelection(engine=value.engine, source=value.source)
    if isinstance(value, Mapping):
        value = value.get("engine")
    if isinstance(value, str) and not value.strip():
        value = None  # an empty setting is not a choice
    if value is not None:
        return RuntimeSelection(engine=normalize_engine(value), source="explicit")

    settings = os.environ if environ is None else environ
    raw = settings.get("ODESSY_RUNTIME_ENGINE")
    if raw is None or not str(raw).strip():
        return RuntimeSelection(engine=DEFAULT_ENGINE, source="default")
    return RuntimeSelection(engine=normalize_engine(raw), source="explicit")


def require_available(selection: RuntimeSelection) -> RuntimeSelection:
    """Raise :class:`RuntimeUnavailable` when the selected engine has no backend."""
    if selection.engine not in _BACKENDS:
        raise RuntimeUnavailable(
            selection.engine,
            "no backend is registered in this build",
            detail=f"available: {', '.join(available_engines())}",
        )
    return selection


def build_runtime(session: Any, selection: RuntimeSelection) -> RuntimeSession:
    """Build the session's engine binding. Called exactly once, at creation."""
    require_available(selection)
    return _BACKENDS[selection.engine](session)


def resolve_and_build(session: Any, value: Any = None, *,
                      environ: Mapping[str, str] | None = None) -> tuple[RuntimeSelection, RuntimeSession]:
    selection = require_available(resolve_selection(value, environ=environ))
    return selection, _BACKENDS[selection.engine](session)


def _reset_registry_for_tests() -> None:  # pragma: no cover - test helper
    """Restore the built-in registry (used by tests that register a fake engine)."""
    _BACKENDS.clear()
    _BACKENDS["legacy"] = _legacy_builder
