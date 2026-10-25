"""Explicit monitoring selection. Pairing never implies monitoring consent."""
from dataclasses import dataclass
from pathlib import Path
import os
import re


@dataclass(frozen=True)
class MonitoringSettings:
    enabled: bool = False
    host_ids: tuple[str, ...] = ()
    include_local: bool = False
    preset: str = 'default'
    binary: str = ''
    link_binary: str = ''
    state_dir: str = ''

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
        return cls(flag('ODESSY_MONITORING'), hosts, flag('ODESSY_MONITOR_LOCAL'), preset,
                   env.get('ODESSY_MONITOR_BIN', ''), env.get('ODESSY_LINK_BIN', ''),
                   env.get('ODESSY_MONITOR_STATE_DIR', str(Path.home() / '.odessy' / 'monitoring')))

    def wire(self):
        return {'monitored_host_ids': list(self.host_ids), 'include_local': self.include_local, 'preset': self.preset}
