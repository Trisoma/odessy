"""Installed versioned layouts must find their packaged monitor/link helpers.

The wheel is imported from ``<root>/versions/<id>/Lib/site-packages``, one directory
deeper than the checkout layout, while the Go monitor and Rust link helpers are copied
to ``<version>/monitor`` and ``<version>/link``. A checkout-root-only lookup finds
nothing there, so SSH and paired monitoring cannot start in an installed desktop.
"""

import pytest

from odessy.monitoring import settings as settings_module


@pytest.fixture(autouse=True)
def no_path_helpers(monkeypatch):
    monkeypatch.setattr(settings_module.shutil, "which", lambda _: None)


def _installed_version(tmp_path):
    version = tmp_path / 'versions' / 'v1'
    source = version / 'Lib' / 'site-packages' / 'odessy' / 'monitoring' / 'settings.py'
    source.parent.mkdir(parents=True)
    source.write_text('')
    return version, source


def test_installed_version_resolves_monitor_and_link(tmp_path, monkeypatch):
    import odessy.tools.paired as paired

    version, source = _installed_version(tmp_path)
    monitor = version / 'monitor' / 'odessy-monitor.exe'
    monitor.parent.mkdir()
    monitor.write_bytes(b'MZ monitor')
    link = version / 'link' / 'odessy-link.exe'
    link.parent.mkdir()
    link.write_bytes(b'MZ link')

    def no_checkout_build(env=None):
        raise ValueError('no checkout build')

    monkeypatch.setattr(paired, 'link_binary', no_checkout_build)
    monkeypatch.setattr(settings_module, '__file__', str(source))

    assert settings_module.resolve_monitor_binary({}) == str(monitor)
    assert settings_module.resolve_link_binary({}) == str(link)


def test_checkout_layout_is_still_resolved(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    source = repo / 'src' / 'odessy' / 'monitoring' / 'settings.py'
    source.parent.mkdir(parents=True)
    source.write_text('')
    monitor = repo / 'build' / 'local-desktop' / 'bin' / 'odessy-monitor.exe'
    monitor.parent.mkdir(parents=True)
    monitor.write_bytes(b'MZ monitor')

    monkeypatch.setattr(settings_module, '__file__', str(source))

    assert settings_module.resolve_monitor_binary({}) == str(monitor)


def test_posix_installed_version_resolves_helpers(tmp_path, monkeypatch):
    import odessy.tools.paired as paired

    monkeypatch.setattr(settings_module.shutil, 'which', lambda _: None)
    monkeypatch.setattr(paired, 'link_binary', lambda _env: None)
    for lib in ('lib', 'lib64'):
        for py in ('python3.10', 'python3.12', 'python3.13'):
            version = tmp_path / lib / py
            source = version / lib / py / 'site-packages/odessy/monitoring/settings.py'
            source.parent.mkdir(parents=True)
            source.write_text('')
            for component, binary in (('monitor', 'odessy-monitor'), ('link', 'odessy-link')):
                path = version / component / binary
                path.parent.mkdir()
                path.write_bytes(b'fixture')
            monkeypatch.setattr(settings_module, '__file__', str(source))
            assert settings_module.resolve_monitor_binary({}) == str(version / 'monitor/odessy-monitor')
            assert settings_module.resolve_link_binary({}) == str(version / 'link/odessy-link')
