"""Prompt 17: the SSH compatibility spike's local, no-network contract.

These checks exercise the parts one Windows laptop can prove without a remote host:
OpenSSH configuration resolution, known-host identity, the restrictive command shape,
the fixed read-only status parser, and the capability matrix. They deliberately do
not claim that SSH authentication, remote execution or Stop works.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "experiments" / "restructure" / "ssh_compatibility"))

import ssh_compat  # noqa: E402

SSH = shutil.which("ssh")
SSH_KEYGEN = shutil.which("ssh-keygen")
needs_ssh = pytest.mark.skipif(SSH is None, reason="OpenSSH client is not installed")
needs_keygen = pytest.mark.skipif(SSH_KEYGEN is None, reason="ssh-keygen is not installed")


def write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_aliases_skip_patterns_and_follow_include(tmp_path):
    extra = write_config(tmp_path / "extra.conf", "Host included-host\n  HostName 192.0.2.11\n")
    config = write_config(
        tmp_path / "config",
        "Include extra.conf\n"
        "Host demo *.wild !blocked demo2\n"
        "  HostName 192.0.2.10\n"
        "# a comment\n"
        "Host *\n"
        "  User everyone\n",
    )
    assert set(ssh_compat.list_aliases(config)) == {"included-host", "demo", "demo2"}


@needs_ssh
def test_resolve_alias_uses_the_effective_configuration(tmp_path):
    config = write_config(
        tmp_path / "config",
        "Host demo\n"
        "  HostName 192.0.2.10\n"
        "  User tester\n"
        "  Port 2222\n",
    )
    resolved = ssh_compat.resolve_alias("demo", ssh=SSH, config_path=config)
    assert resolved.hostname == "192.0.2.10"
    assert resolved.user == "tester"
    assert resolved.port == 2222
    assert resolved.endpoint == "192.0.2.10:2222"
    assert resolved.proxy_jump is None
    assert resolved.config_path == str(config)


@needs_keygen
def test_host_identity_reads_only_known_hosts(tmp_path):
    key = tmp_path / "hostkey"
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                   check=True, capture_output=True)
    public = (tmp_path / "hostkey.pub").read_text(encoding="utf-8").strip()
    known = tmp_path / "known_hosts"
    known.write_text(f"demo.example {public}\n", encoding="utf-8")

    alias = ssh_compat.SshAlias(alias="demo", hostname="demo.example", user="tester")
    identity = ssh_compat.host_identity(alias, ssh_keygen=SSH_KEYGEN, known_hosts=known)
    assert identity.known is True
    assert len(identity.keys) == 1
    assert identity.keys[0].key_type == "ed25519"
    assert identity.keys[0].fingerprint.startswith("SHA256:")

    absent = ssh_compat.host_identity(
        ssh_compat.SshAlias(alias="demo", hostname="absent.example", user="tester"),
        ssh_keygen=SSH_KEYGEN, known_hosts=known)
    assert absent.known is False


def test_ssh_command_is_restrictive_and_ordered():
    alias = ssh_compat.SshAlias(alias="demo", hostname="192.0.2.10", user="tester")
    argv = ssh_compat.ssh_argv(alias, "printf ok")
    assert argv[0] == "ssh"
    options = set(argv[1:-2])
    for expected in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ForwardAgent=no",
                     "ClearAllForwardings=yes", "ControlMaster=no"):
        assert expected in options
    assert argv[-2] == "demo"
    assert argv[-1] == "printf ok"
    assert not any("password" in part.lower() for part in argv)


def test_ssh_command_uses_an_explicit_config_file():
    alias = ssh_compat.SshAlias(
        alias="demo", hostname="192.0.2.10", user="tester",
        config_path="C:/tmp/p17-config")
    argv = ssh_compat.ssh_argv(alias, "printf ok")
    assert argv[1:3] == ("-F", "C:/tmp/p17-config")
    assert argv[-2] == "demo"


@pytest.mark.parametrize("alias", ["-oProxyCommand=calc", "a b", "*", "", "host\nother"])
def test_unsafe_alias_is_refused(alias):
    with pytest.raises(ssh_compat.SshConfigError):
        ssh_compat.ssh_argv(ssh_compat.SshAlias(alias=alias, hostname="h", user="u"), "true")


def test_remote_command_quotes_workspace_and_rejects_bad_values():
    command = ssh_compat.remote_stdio_command(
        "/opt/odessy/bin/python", "/home/tester/my work")
    assert "--workspace '/home/tester/my work'" in command
    assert command.startswith("/opt/odessy/bin/python -m odessy.tools.stdio_server")
    assert "--mode read-only" in command
    assert "--isolation preferred" in command
    assert "--network deny" in command

    with pytest.raises(ssh_compat.SshConfigError):
        ssh_compat.remote_stdio_command("python3", "/home/tester/work")
    with pytest.raises(ssh_compat.SshConfigError):
        ssh_compat.remote_stdio_command("/opt/python", "relative/work")
    with pytest.raises(ssh_compat.SshConfigError):
        ssh_compat.remote_stdio_command("/opt/python", "/home/tester/work", mode="root")


def test_status_parser_accepts_only_a_complete_sample():
    raw = (
        "0.52 0.58 0.59 1/234 5678\n"
        "---\n"
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/sda1 1000000 370000 630000 37% /\n"
    )
    sample = ssh_compat.parse_status(raw, host_key="SHA256:test")
    assert sample.load_1m == pytest.approx(0.52)
    assert sample.load_5m == pytest.approx(0.58)
    assert sample.load_15m == pytest.approx(0.59)
    assert sample.disk_used_percent == 37
    assert sample.source == "ssh"
    assert sample.host_key == "SHA256:test"


@pytest.mark.parametrize("raw", [
    "0.1 0.2 0.3\n",
    "nan 0.2 0.3\n---\n/dev/sda1 1 1 1 10% /\n",
    "0.1 0.2 0.3\n---\nFilesystem 1024-blocks Used Available Capacity Mounted on\n",
    "0.1 0.2 0.3\n---\n/dev/sda1 1 1 1 101% /\n",
    "0.1 0.2 0.3\n---\n/dev/sda1 1 1 1 full /\n",
])
def test_status_parser_never_invents_values(raw):
    result = ssh_compat.parse_status(raw)
    assert isinstance(result, ssh_compat.Unavailable)
    assert result.code.startswith("SSH_")


def test_capabilities_require_separate_evidence():
    login_only = ssh_compat.capabilities(
        transport=True,
        monitoring_enabled=False, monitoring_ok=True,
        execution_enabled=False, tools_listed=True,
        workspace_ok=False,
    )
    assert login_only.as_dict() == {
        "transport": True, "monitoring": False, "execution": False, "workspace": False,
    }

    granted = ssh_compat.capabilities(
        transport=True,
        monitoring_enabled=True, monitoring_ok=True,
        execution_enabled=True, tools_listed=True,
        workspace_ok=True,
    )
    assert granted.as_dict() == {
        "transport": True, "monitoring": True, "execution": True, "workspace": True,
    }


def test_server_config_carries_method_identity_and_no_environment():
    alias = ssh_compat.SshAlias(alias="jetson", hostname="192.0.2.10", user="tester")
    identity = ssh_compat.HostIdentity(
        alias="jetson", hostname="192.0.2.10", port=22,
        keys=(ssh_compat.HostKey("ed25519", "SHA256:test"),))
    computer = ssh_compat.Computer(alias=alias, identity=identity, display_name="jetson")

    config = ssh_compat.server_config(
        computer, "/home/tester/odessy/.venv/bin/python", "/home/tester/work")

    assert config.transport == "stdio"
    assert config.env == ()
    assert config.name.startswith("ssh-")
    assert config.target_metadata["transport"] == "ssh"
    assert config.target_metadata["ssh_alias"] == "jetson"
    assert config.target_metadata["ssh_host_key_fingerprint"] == "SHA256:test"
    assert config.target_metadata["workspace"] == "/home/tester/work"
    assert "odessy.tools.stdio_server" in config.command[-1]
    assert not any("token" in part.lower() or "key=" in part.lower()
                   for part in config.command)


def test_monitoring_command_is_fixed():
    assert ssh_compat.monitoring_command() == (
        "cat /proc/loadavg; printf '\\n---\\n'; df -P -k /"
    )
