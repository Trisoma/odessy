# Benchmark config parser
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class BenchmarkProfile:
    name: str
    component: str
    execution_path: str
    lifecycle: str
    onnx_provider: str | None = None
    requested_provider_mode: str | None = None
    provider_priority: list[str] | None = None
    strict_provider: bool = False
    engine_cache: bool = False
    model_backend: str | None = None
    model_name: str | None = None
    model_precision: str | None = None
    batch_size: int | None = None
    enabled: bool = True

    @classmethod
    def from_mapping(
        cls,
        name: str,
        raw: dict[str, Any],
        *,
        defaults: dict[str, Any] | None = None,
    ) -> "BenchmarkProfile":
        merged = dict(defaults or {})
        merged.update(raw)
        component = _required_text(merged, "component", name)
        execution_path = _required_text(merged, "execution_path", name)
        lifecycle = _required_text(merged, "lifecycle", name)
        if lifecycle not in {"cold", "warm"}:
            raise ValueError(f"Profile {name!r} has unsupported lifecycle {lifecycle!r}")
        batch_size = merged.get("batch_size")
        if batch_size is not None and (not isinstance(batch_size, int) or batch_size < 1):
            raise ValueError(f"Profile {name!r} batch_size must be a positive integer")
        return cls(
            name=name,
            component=component,
            execution_path=execution_path,
            lifecycle=lifecycle,
            onnx_provider=_optional_text(
                merged,
                "onnx_provider",
                fallback=_optional_text(merged, "requested_provider_mode"),
            ),
            requested_provider_mode=_optional_text(merged, "requested_provider_mode"),
            provider_priority=_optional_list(merged, "provider_priority"),
            strict_provider=bool(merged.get("strict_provider", False)),
            engine_cache=bool(merged.get("engine_cache", False)),
            model_backend=_optional_text(merged, "model_backend"),
            model_name=_optional_text(merged, "model_name"),
            model_precision=_optional_text(merged, "model_precision"),
            batch_size=batch_size,
            enabled=bool(merged.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.name,
            "component": self.component,
            "execution_path": self.execution_path,
            "lifecycle": self.lifecycle,
            "onnx_provider": self.onnx_provider,
            "requested_provider_mode": self.requested_provider_mode,
            "provider_priority": self.provider_priority,
            "strict_provider": self.strict_provider,
            "engine_cache": self.engine_cache,
            "model_backend": self.model_backend,
            "model_name": self.model_name,
            "model_precision": self.model_precision,
            "batch_size": self.batch_size,
            "enabled": self.enabled,
        }

def load_profiles(path: Path) -> dict[str, BenchmarkProfile]:
    with path.open("rb") as handle:
        raw = _load_toml(handle)
    profile_table = raw.get("profiles")
    if not isinstance(profile_table, dict):
        raise ValueError(f"Manifest {path} must contain a [profiles] table")

    profiles: dict[str, BenchmarkProfile] = {}
    defaults = {
        key: value
        for key, value in raw.items()
        if key not in {"profiles", "version", "experiment"}
    }
    for name, values in profile_table.items():
        if not isinstance(name, str) or not isinstance(values, dict):
            raise ValueError("Each profile must be a named TOML table")
        if name in profiles:
            raise ValueError(f"Duplicate benchmark profile: {name}")
        profiles[name] = BenchmarkProfile.from_mapping(name, values, defaults=defaults)
    return profiles


def _load_toml(handle: Any) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10 uses the tomli backport.
        import tomli as tomllib
    return tomllib.load(handle)


def _required_text(raw: dict[str, Any], key: str, profile_name: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Profile {profile_name!r} requires non-empty {key!r}")
    return value.strip()

def _optional_text(
    raw: dict[str, Any],
    key: str,
    *,
    fallback: str | None = None,
) -> str | None:
    value = raw.get(key, fallback)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Profile field {key!r} must be a non-empty string")
    return value.strip()

def _optional_list(raw: dict[str, Any], key: str) -> list[str] | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Profile field {key!r} must be a list of strings")
    return list(value)
