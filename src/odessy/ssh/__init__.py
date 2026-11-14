"""OpenSSH identity/config support for the application's SSH connection method."""

from .identity import SshError, SshHost, SshIdentity, read_identity, resolve_host

__all__ = ["SshError", "SshHost", "SshIdentity", "read_identity", "resolve_host"]
