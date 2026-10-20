from __future__ import annotations

import os
from pathlib import Path

#: Env var naming one config file to load instead of the default pair.
CONFIG_FILE_ENV = "ODESSY_CONFIG_FILE"


def default_config_file() -> Path:
    """The repository's committed defaults."""

    return Path(__file__).resolve().parents[2] / "config" / "odessy.env"


def user_config_file() -> Path:
    """This user's own settings, kept out of the repository.

    Machine-specific values (model paths, the laptop's address in a two-machine setup)
    belong here, not in the committed ``config/odessy.env``.
    """

    return Path.home() / ".config" / "odessy" / "odessy.env"


def config_files() -> list[Path]:
    """The config files to load, highest precedence first.

    ``ODESSY_CONFIG_FILE`` names exactly one file and replaces both defaults, which is
    what tests and CI rely on to keep every developer config out of a run.
    """

    explicit = os.environ.get(CONFIG_FILE_ENV)
    if explicit:
        return [Path(explicit)]
    return [user_config_file(), default_config_file()]


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
    """Load config values into ``os.environ`` without overriding what is already set.

    Precedence: the environment, then the user's file, then the committed defaults.
    """

    paths = [Path(path)] if path is not None else config_files()
    for config_path in paths:
        for key, value in read_env_file(config_path).items():
            os.environ.setdefault(key, value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
