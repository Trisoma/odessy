"""Accept-tap LAN pairing for the execution host.

A coordinator on the LAN asks this host for a token; the person sitting at the
host decides. Nothing here is trusted from the network: the request only creates
a pending question, and only the bearer-authenticated ``/v1/pairing/approve``
route -- reachable by the local user alone -- can answer it.

``zeroconf`` is optional. Importing this module never imports it, and
:func:`register_mdns` degrades to a no-op when it is missing, so the LAN
discovery dependency stays out of the hard requirement set.
"""
from __future__ import annotations

import asyncio
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


#: Concurrent prompts are capped: the request route is unauthenticated, so an
#: uninvited host must not be able to queue up questions for the local user.
MAX_PENDING = 8

#: request_id -> Future[bool] resolved by the local approve route.
_pending: dict[str, asyncio.Future] = {}


def has_room():
    """False when the local user already has the maximum unanswered prompts."""
    return len(_pending) < MAX_PENDING


async def wait_for_approval(request_id, name, ip, timeout=60):
    """Block until the local user accepts or declines this request.

    Returns ``True`` only on acceptance. A timeout, a decline, or a cancelled
    waiter all return ``False``: an unanswered request is never an approval.
    """
    if request_id in _pending:
        raise ValueError('Pairing request is already pending')
    future = asyncio.get_running_loop().create_future()
    _pending[request_id] = future
    try:
        return bool(await asyncio.wait_for(future, timeout))
    except asyncio.TimeoutError:
        return False
    finally:
        # A timeout or a dropped caller must not leave a resolvable approval behind.
        _pending.pop(request_id, None)


def resolve(request_id, accepted):
    """Answer a pending request. Returns False when the id is unknown or settled."""
    future = _pending.get(request_id)
    if future is None or future.done():
        return False
    future.set_result(bool(accepted))
    return True


def register_mdns(app, port, *, instance=None):
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
    address = lan_ip()
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
            properties={'version': '1', 'hostname': hostname},
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
