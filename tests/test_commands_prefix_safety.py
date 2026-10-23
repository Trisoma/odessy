import pytest

from odessy.config.commands import CommandsConfigError, _parse_command


@pytest.mark.parametrize("command", [
    "bash -c", "sh -c", "powershell -Command", "cmd /c",
    "python -c", "node -e", "npm run", "uv run", "go run", "cargo run",
    "BASH.EXE -c echo", r"C:\Windows\System32\cmd.exe /C echo",
    "/usr/bin/python3 -c print", "pwsh -EncodedCommand abc", "node --eval code",
    "bash -lc echo", "python -m", "npm exec", "uv run --",
    "/usr/bin/bash", r"C:\Python\python.exe", "PYTHON3.12 -c print",
])
def test_rejects_unconstrained_prefixes(command):
    with pytest.raises(CommandsConfigError):
        _parse_command("test", command)


@pytest.mark.parametrize("command", [
    "python -m pytest", "npm run build", "uv run pytest", "go run main.go",
    "cargo run --bin app", "python scripts/check.py", "npm test", "pytest",
])
def test_allows_named_tasks(command):
    assert _parse_command("test", command) == tuple(command.lower().split())


@pytest.mark.parametrize("command", ["cmd /d", "powershell -ExecutionPolicy Bypass", "python -W ignore"])
def test_option_values_are_not_fixed_scripts(command):
    from odessy.config.commands import _parse_command, CommandsConfigError
    with pytest.raises(CommandsConfigError):
        _parse_command("test", command)
