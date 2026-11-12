"""Explicit monitoring selection. Pairing never implies monitoring consent."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import re
import shutil


def _first_file(candidates):
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return ''


def resolve_monitor_binary(env=None) -> str:
    """Find the packaged/source monitor without requiring an environment edit."""

    env = os.environ if env is None else env
    explicit = env.get('ODESSY_MONITOR_BIN', '').strip()
    if explicit:
        return explicit
    found = shutil.which('odessy-monitor')
    if found:
        return found
    root = Path(__file__).resolve().parents[3]
    candidates = [
        root / 'build' / 'local-desktop' / 'bin' / 'odessy-monitor.exe',
        root / 'build' / 'local-desktop' / 'bin' / 'odessy-monitor',
        root / 'monitor' / 'odessy-monitor.exe',
        root / 'monitor' / 'odessy-monitor',
    ]
    portable = env.get('ODESSY_PORTABLE_ROOT', '').strip()
    if portable:
        candidates.extend([Path(portable) / 'monitor' / 'odessy-monitor.exe',
                           Path(portable) / 'monitor' / 'odessy-monitor'])
    return _first_file(candidates)


def resolve_link_binary(env=None) -> str:
    env = os.environ if env is None else env
    explicit = env.get('ODESSY_LINK_BIN', '').strip()
    if explicit:
        return explicit
    try:
        from odessy.tools.paired import link_binary
        path = link_binary(env)
    except (ValueError, OSError):
        path = None
    if path is not None and path.is_file():
        return str(path)
    root = Path(__file__).resolve().parents[3]
    candidates = [
        root / 'link' / 'odessy-link.exe',
        root / 'link' / 'odessy-link',
        root / 'build' / 'odessy-link' / 'odessy-link.exe',
        root / 'build' / 'odessy-link' / 'odessy-link',
    ]
    portable = env.get('ODESSY_PORTABLE_ROOT', '').strip()
    if portable:
        candidates.extend([Path(portable) / 'link' / 'odessy-link.exe',
                           Path(portable) / 'link' / 'odessy-link'])
    return _first_file(candidates)


@dataclass(frozen=True)
class MonitoringSettings:
    enabled: bool = False
    host_ids: tuple[str, ...] = ()
    include_local: bool = False
    preset: str = 'default'
    binary: str = ''
    link_binary: str = ''
    state_dir: str = ''
    ssh_alias: str = ''
    ssh_host_id: str = ''
    ssh_display_name: str = ''
    ssh_config: str = ''
    ssh_monitoring: bool = False
    ssh_binary: str = ''

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        def flag(key):
            value = env.get(key, 'off').lower().strip()
            if value not in ('on', 'off', 'true', 'false', '1', '0'):
                raise ValueError(f'{key} must be on or off')
            return value in ('on', 'true', '1')
        hosts = tuple(v.strip() for v in env.get('ODESSY_MONITOR_HOSTS', '').split(',') if v.strip())
        if len(hosts) > 32 or len(set(hosts)) != len(hosts) or any(not re.fullmatch(r'host_[a-zA-Z0-9_]{1,128}', h) for h in hosts):
            raise ValueError('Invalid monitoring host IDs')
        preset = env.get('ODESSY_MONITOR_PRESET', 'default')
        if preset not in ('default', 'low-power'):
            raise ValueError('Invalid monitoring preset')
        connection = flag('ODESSY_SSH_COMPUTER')
        ssh_monitoring = flag('ODESSY_SSH_MONITORING')
        alias = env.get('ODESSY_SSH_ALIAS', '').strip()
        host_id = env.get('ODESSY_SSH_HOST_ID', '').strip()
        if connection or ssh_monitoring:
            if not alias:
                raise ValueError('ODESSY_SSH_ALIAS is required when the SSH computer is on')
            if not host_id:
                host_id = 'host_' + hashlib.sha256(alias.encode('utf-8')).hexdigest()
            if not re.fullmatch(r'host_[0-9a-f]{64}', host_id):
                raise ValueError('ODESSY_SSH_HOST_ID must be host_ followed by 64 lowercase hex characters')
            if ssh_monitoring:
                if host_id not in hosts:
                    hosts = (*hosts, host_id)
                if len(hosts) > 32:
                    raise ValueError('SSH host would exceed the 32-host monitoring limit')
        enabled = flag('ODESSY_MONITORING') or connection or ssh_monitoring
        return cls(
            enabled=enabled, host_ids=hosts,
            include_local=flag('ODESSY_MONITOR_LOCAL'), preset=preset,
            binary=resolve_monitor_binary(env),
            link_binary=resolve_link_binary(env),
            state_dir=env.get('ODESSY_MONITOR_STATE_DIR', str(Path.home() / '.odessy' / 'monitoring')),
            ssh_alias=alias, ssh_host_id=host_id,
            ssh_display_name=env.get('ODESSY_SSH_DISPLAY_NAME', '').strip() or alias,
            ssh_config=env.get('ODESSY_SSH_CONFIG', '').strip(),
            ssh_monitoring=ssh_monitoring,
            ssh_binary=env.get('ODESSY_SSH_BIN', '').strip(),
        )

    def wire(self):
        return {'monitored_host_ids': list(self.host_ids), 'include_local': self.include_local, 'preset': self.preset}
