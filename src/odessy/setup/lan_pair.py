"""Coordinator side of accept-tap pairing: find an execution host, then ask it.

Discovery is mDNS (``_odessy-exec._tcp.local.``). Pairing is one HTTP request that
the laptop's own user answers by hand, so this module never decides trust: it
receives a bearer token only when that person accepts, and a decline is final
until the operator tries again.

``zeroconf`` is optional. Scanning without it returns an empty list rather than
failing, because a host can still be paired by typing its address.
"""

from __future__ import annotations

import asyncio
import json
import time
import threading
from urllib.parse import urlsplit
import urllib.error
import urllib.request

#: Kept equal to ``odessy.application.pairing.SERVICE_TYPE`` by a test: the laptop
#: announces this name and the coordinator looks for it.
SERVICE_TYPE = '_odessy-exec._tcp.local.'
#: The laptop waits 60 s for its user to answer, so this client waits longer: the
#: person's decision, not a socket timeout here, should end the request.
REQUEST_TIMEOUT = 75.0
#: Bounds for a reply that is only ever a small JSON object.
MAX_RESPONSE_BYTES = 64 * 1024
_SCAN_INTERVAL = 0.05
#: A pairing goes to the host on this LAN directly. An ``http_proxy`` from the
#: environment would otherwise intercept it and never reach the laptop.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class PairingDeclined(Exception):
    """The laptop's user declined, or nobody answered before the host gave up."""


class PairingFailed(Exception):
    """The host was unreachable, or did not return a usable pairing."""


def scan_execution_hosts(timeout: float = 3.0) -> list[dict]:
    """Execution hosts announcing themselves on this LAN, within ``timeout``.

    Returns ``[{"name", "host", "port"}, ...]`` sorted by name. Each host is
    reported once; its address is an IPv4 literal where the announcement has one,
    which is what the plain-HTTP request needs.
    """
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []

    found: dict[str, dict] = {}
    lock = threading.Lock()

    class Collector:
        """zeroconf resolves a name on a worker thread; this only records it."""

        def add_service(self, zeroconf, service_type, name):
            try:
                info = zeroconf.get_service_info(service_type, name)
            except Exception:
                return
            entry = _entry(info, name)
            if entry is not None:
                with lock:
                    found[entry.get('id', entry['name'])] = entry

        update_service = add_service

        def remove_service(self, zeroconf, service_type, name):
            with lock:
                for key in [key for key, row in found.items() if row['name'] == _label(name)]:
                    found.pop(key, None)

    zeroconf = Zeroconf()
    browser = None
    try:
        browser = ServiceBrowser(zeroconf, SERVICE_TYPE, Collector())
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            time.sleep(_SCAN_INTERVAL)
    finally:
        if browser is not None:
            browser.cancel()
        zeroconf.close()
    with lock:
        return [found[name] for name in sorted(found)]


async def request_pairing(host: str, port: int, coordinator_name: str, *, device_id=None) -> dict:
    """Ask the execution host to pair; return ``{"token", "url"}``.

    Raises :class:`PairingDeclined` when the laptop's user said no or let the
    request expire, and :class:`PairingFailed` for anything else.
    """
    address = (host or '').strip()
    name = (coordinator_name or '').strip()
    if not address:
        raise PairingFailed('a host address is required')
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise PairingFailed('a port from 1 to 65535 is required')
    if not name or len(name) > 64:
        raise PairingFailed('a coordinator name of 1-64 characters is required')
    request = urllib.request.Request(
        f'http://{address}:{port}/v1/pairing/request',
        data=json.dumps({'name': name, **({'device_id': device_id} if device_id else {})}).encode('utf-8'),
        method='POST',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    # urllib blocks; keep the event loop free while the laptop's user decides.
    payload = await asyncio.to_thread(_exchange, request)
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise PairingFailed('the host returned an unreadable response') from error
    token, url = (data.get('credential') or data.get('token'), data.get('url')) if isinstance(data, dict) else (None, None)
    if not isinstance(token, str) or not token or not isinstance(url, str) or not url:
        raise PairingFailed('the host accepted the request but returned no token or URL')
    if device_id is not None:
        try:
            target = urlsplit(url)
            valid = (data.get('device_id') == device_id and target.scheme == 'http'
                     and target.hostname == address and target.port and 1 <= target.port <= 65535
                     and target.path == '/mcp' and not target.username and not target.password
                     and not target.query and not target.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise PairingFailed('The execution endpoint changed; refresh discovery and reconnect')
        return {'credential': token, 'url': url, 'workspace': data.get('workspace'), 'device_id': device_id}
    return {'token': token, 'url': url}  # Legacy CLI compatibility; new UI never prints tokens.


def _exchange(request: urllib.request.Request) -> bytes:
    """One request/response, with every failure mapped to this module's errors."""
    try:
        with _DIRECT.open(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 403:
            raise PairingDeclined('the laptop user declined or did not respond') from error
        raise PairingFailed(f'the host refused the request (HTTP {error.code})') from error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, 'reason', error)
        raise PairingFailed(f'could not reach the host: {reason}') from error
    if status != 200:
        raise PairingFailed(f'the host answered with HTTP {status}')
    if len(body) > MAX_RESPONSE_BYTES:
        raise PairingFailed('the host returned more than 64 KiB')
    return body


def _label(name: str) -> str:
    """The instance label of an mDNS name, without its service type."""
    suffix = '.' + SERVICE_TYPE
    return name[:-len(suffix)] if name.endswith(suffix) else name


def _entry(info, name: str) -> dict | None:
    """Turn one resolved service into a reachable host, or None if it is unusable."""
    if info is None:
        return None
    port = getattr(info, 'port', None)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    try:
        addresses = list(info.parsed_addresses())
    except Exception:
        addresses = []
    host = next((value for value in addresses if ':' not in value), '')
    if not host:
        # Without an IPv4 literal, the announced DNS name is the only option.
        host = (getattr(info, 'server', '') or '').rstrip('.')
    if not host:
        return None
    label = _label(name) or name
    properties = getattr(info, 'properties', {}) or {}
    device_id = properties.get(b'device_id') or properties.get('device_id')
    if isinstance(device_id, bytes):
        device_id = device_id.decode('ascii', errors='replace')
    return {'name': label, 'host': host, 'port': port,
            **({'id': device_id} if isinstance(device_id, str) and device_id else {})}
