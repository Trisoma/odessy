"""LAN discovery for the execution host.

This module only announces the host: the decision to accept a coordinator is made
on that host's console by :mod:`odessy.application.executor`, and the credential it
hands out is described by :mod:`odessy.application.execution_access`. Nothing here
holds approval state or mints secrets.

``zeroconf`` is optional. Importing this module never imports it, and
:func:`register_mdns` degrades to a no-op when it is missing, so the LAN
discovery dependency stays out of the hard requirement set.
"""
from __future__ import annotations

import ipaddress
import socket

#: mDNS service type announced by the execution host.
SERVICE_TYPE = '_odessy-exec._tcp.local.'


def is_loopback(host):
    """True when *host* is a loopback address; unknown values are not loopback."""
    try:
        return ipaddress.ip_address(host or '').is_loopback
    except ValueError:
        return False


def lan_ip():
    """First non-loopback IPv4 address of this machine, or ``None``.

    This is the address a coordinator on the same network can reach, so it is
    what a pairing response advertises. ``None`` means the host has no LAN IPv4
    address to offer.
    """
    try:
        candidates = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        return None
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback:
            return candidate
    return None


def register_mdns(app, port, *, instance=None, address=None, device_id=None):
    """Announce ``_odessy-exec._tcp.local.`` and record the serving port.

    The port is recorded whether or not zeroconf is installed, because the
    pairing response has to state the URL a coordinator should call. Returns the
    ``Zeroconf`` instance, or ``None`` when nothing was announced.
    """
    app.state.pairing_port = port
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        return None  # Optional dependency: LAN discovery is best effort.
    address = address or lan_ip()
    if address is None:
        return None  # No LAN IPv4 address to advertise.
    hostname = socket.gethostname()
    zeroconf = None
    try:
        info = ServiceInfo(
            SERVICE_TYPE,
            f'{instance or hostname}.{SERVICE_TYPE}',
            addresses=[socket.inet_aton(address)],
            port=port,
            properties={'version': '1', 'hostname': hostname,
                        **({'device_id': device_id} if device_id else {})},
            server=f'{hostname}.local.',
        )
        zeroconf = Zeroconf()
        zeroconf.register_service(info)
    except Exception:
        # A network without multicast must not stop the HTTP API from serving.
        if zeroconf is not None:
            try:
                zeroconf.close()
            except Exception:
                pass
        return None
    app.state.zeroconf = zeroconf
    app.state.zeroconf_info = info
    return zeroconf
