"""Installed versioned layouts must find their packaged monitor/link helpers.

The wheel is imported from ``<root>/versions/<id>/Lib/site-packages``, one directory
deeper than the checkout layout, while the Go monitor and Rust link helpers are copied
to ``<version>/monitor`` and ``<version>/link``. A checkout-root-only lookup finds
nothing there, so SSH and paired monitoring cannot start in an installed desktop.
"""

from odessy.monitoring import settings as settings_module


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
