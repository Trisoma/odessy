from __future__ import annotations

import os
from pathlib import Path


def default_config_file() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "odessy.env"


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        if key:
            values[key] = os.path.expanduser(os.path.expandvars(value))
    return values


def load_config_env(path: str | Path | None = None) -> None:
    config_path = Path(path or os.environ.get("ODESSY_CONFIG_FILE") or default_config_file())
    for key, value in read_env_file(config_path).items():
        os.environ.setdefault(key, value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
