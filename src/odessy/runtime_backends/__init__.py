"""Engine selection seam. See ``contracts`` for the types, ``selection`` for the rules."""

from .contracts import (
    CAPABILITIES,
    DEFAULT_ENGINE,
    ENGINES,
    HARNESS_CAPABILITIES,
    LEGACY_CAPABILITIES,
    EngineName,
    RuntimeCapabilities,
    RuntimeEngineLocked,
    RuntimeEvent,
    RuntimeSelectionError,
    RuntimeSelectionFailure,
    RuntimeSession,
    RuntimeUnavailable,
)
from .legacy import LegacyRuntime
from .selection import (
    RuntimeSelection,
    available_engines,
    build_runtime,
    normalize_engine,
    register_backend,
    require_available,
    resolve_and_build,
    resolve_selection,
)

__all__ = [
    "CAPABILITIES",
    "DEFAULT_ENGINE",
    "ENGINES",
    "HARNESS_CAPABILITIES",
    "LEGACY_CAPABILITIES",
    "EngineName",
    "LegacyRuntime",
    "RuntimeCapabilities",
    "RuntimeEngineLocked",
    "RuntimeEvent",
    "RuntimeSelection",
    "RuntimeSelectionError",
    "RuntimeSelectionFailure",
    "RuntimeSession",
    "RuntimeUnavailable",
    "available_engines",
    "build_runtime",
    "normalize_engine",
    "register_backend",
    "require_available",
    "resolve_and_build",
    "resolve_selection",
]
