from __future__ import annotations

import os
import re
import tempfile
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path

_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

#: Env var naming one config file to load instead of the default pair.
CONFIG_FILE_ENV = "ODESSY_CONFIG_FILE"


def default_config_file() -> Path:
    """The repository's committed defaults."""

    return Path(__file__).resolve().parents[3] / "config" / "odessy.env"


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


def validate_env_key(key: str) -> None:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError('Config key must be a plain environment name')


def validate_env_value(value: str) -> None:
    """Only representations that read_env_file() returns unchanged are accepted."""

    if not isinstance(value, str):
        raise ValueError('Config value must be text')
    if not value or value != value.strip():
        raise ValueError('Config value must be non-empty without surrounding whitespace')
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        raise ValueError('Config value must not contain control characters')
    if value[0] in {"'", '"'} or value[-1] in {"'", '"'}:
        raise ValueError('Config value must not start or end with a quote')
    if '$' in value:
        raise ValueError('Config value must not contain environment expansion syntax')


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_env_text(path: Path) -> str | None:
    target = Path(path)
    if target.is_symlink():
        raise ValueError('Config file must not be a symbolic link')
    return target.read_text(encoding='utf-8') if target.exists() else None


def restore_env_text(path: Path, text: str | None) -> None:
    """Restore a previously captured file as one atomic operation."""

    target = Path(path)
    if target.is_symlink():
        raise ValueError('Config file must not be a symbolic link')
    with _lock_for(target):
        if text is None:
            if target.exists():
                target.unlink()
            return
        _atomic_text(target, text)


def update_env_values(path: Path, values: Mapping[str, str],
                      *, remove: Iterable[str] = ()) -> Path:
    """Update only the named keys in a user config file, preserving comments/order.

    This is the existing config store, not a second registry. It refuses a symlink,
    validates every key/value before touching the file, serializes writers and uses
    a collision-safe temporary file.
    """

    target = Path(path)
    if target.is_symlink():
        raise ValueError('Config file must not be a symbolic link')
    normalized = dict(values)
    drop = set(remove)
    for key in (*normalized.keys(), *drop):
        validate_env_key(key)
    for value in normalized.values():
        validate_env_value(value)
    with _lock_for(target):
        existing = target.read_text(encoding='utf-8').splitlines() if target.exists() else []
        keys = {key for key in normalized}
        seen: set[str] = set()
        output: list[str] = []
        for raw in existing:
            stripped = raw.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                output.append(raw)
                continue
            key = stripped.split('=', 1)[0].strip()
            if key in drop:
                continue
            if key in normalized:
                output.append(f'{key}={normalized[key]}')
                seen.add(key)
            else:
                output.append(raw)
        for key in sorted(keys - seen):
            output.append(f'{key}={normalized[key]}')
        _atomic_text(target, '\n'.join(output).rstrip() + '\n')
    return target
