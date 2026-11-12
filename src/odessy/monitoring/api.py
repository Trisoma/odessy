"""Read-only routes behind the existing application authentication middleware.

The Go service owns host inventory, polling, freshness, history and the event bus.
Python only projects the public JSON (including the SSH method) and exposes the small
typed connection record for the desktop. It does not poll, merge host lists or run a
second event loop.
"""

from __future__ import annotations

import re

from .supervisor import MonitorHTTPError, MonitorUnavailable


def register(app, monitor):
    from fastapi import Request
    from fastapi.responses import JSONResponse, StreamingResponse

    def current_ssh():
        settings = getattr(monitor, 'settings', None)
        return (getattr(settings, 'ssh_host_id', '') or '',
                getattr(settings, 'ssh_alias', '') or '')

    async def failure(code='MONITOR_UNAVAILABLE', status=503):
        return JSONResponse(
            {'schema_version': 1, 'error': {'code': code, 'message': code,
                                            'retryable': status == 503}},
            status_code=status)

    def host_shape(host):
        if not isinstance(host, dict):
            return host
        host = dict(host)
        ssh_host_id, ssh_alias = current_ssh()
        trust = host.get('trust')
        trust_state = trust if isinstance(trust, str) else (trust or {}).get('state')
        if isinstance(trust, str):
            # The monitoring UI's vocabulary has no SSH trust state. The public
            # shape says unknown (not certificate-paired); the method says SSH.
            state = 'unknown' if trust == 'ssh' else trust
            host['trust'] = {'state': state,
                             'local_revision': host.get('generation', '').split(':', 1)[0] or None}
        if isinstance(host.get('connectivity'), str):
            host['connectivity'] = {'state': host['connectivity'],
                                    'last_seen_at': host.get('last_seen_at'),
                                    'latency_ms': host.get('latency_ms')}
        if 'connection' not in host:
            host['connection'] = {
                'method': 'ssh' if host.get('host_id') == ssh_host_id or trust_state == 'ssh'
                else 'local' if trust_state == 'local'
                else 'odessy-link' if trust_state == 'paired' else 'unknown'
            }
            if host['connection']['method'] == 'ssh':
                host['connection']['alias'] = ssh_alias
        return host

    async def serve(request):
        if monitor is None:
            return await failure()
        suffix = request.url.path.removeprefix('/v1/monitoring')
        internal = '/internal/v1' + (suffix or '/monitoring')
        if suffix.startswith('/hosts/') and not re.fullmatch(
                r'/hosts/host_[A-Za-z0-9_]{1,128}(?:/(metrics|history))?', suffix):
            return await failure('HOST_NOT_FOUND', 404)
        allowed = ({'limit', 'cursor'} if suffix == '/hosts'
                   else {'from', 'to', 'step', 'series'} if suffix.endswith('/history')
                   else set())
        if set(request.query_params) - allowed:
            return await failure('INVALID_REQUEST', 400)
        cursor = request.headers.get('Last-Event-ID')
        if cursor and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}:\d{1,20}', cursor):
            return await failure('INVALID_REQUEST', 400)
        try:
            if suffix == '/events':
                async def events():
                    try:
                        async for chunk in monitor.events(cursor):
                            if await request.is_disconnected():
                                break
                            yield chunk
                    except MonitorUnavailable:
                        yield b'event: resync_required\ndata: {"schema_version":1}\n\n'
                return StreamingResponse(events(), media_type='text/event-stream',
                                         headers={'Cache-Control': 'no-store'})
            query = dict(request.query_params)
            if suffix.endswith('/history') and 'series' in query:
                query['series'] = request.query_params.getlist('series')
            result = await monitor.request('GET', internal, query)
            if suffix == '/hosts':
                result['hosts'] = [host_shape(host) for host in result.get('hosts', [])]
            elif 'host' in result:
                result['host'] = host_shape(result['host'])
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except MonitorHTTPError as error:
            if error.status in (403, 404):
                return await failure('HOST_NOT_FOUND', 404)
            return await failure(error.code, error.status)
        except (MonitorUnavailable, TimeoutError):
            return await failure()

    serve.__annotations__['request'] = Request
    for route in ('', '/hosts', '/hosts/{host_id}', '/hosts/{host_id}/metrics',
                  '/hosts/{host_id}/history', '/events'):
        app.add_api_route('/v1/monitoring' + route, serve, methods=['GET'])

    if monitor is not None:
        async def ssh_computer(request):
            ssh_id, ssh_alias = current_ssh()
            if not ssh_id:
                return await failure('HOST_NOT_FOUND', 404)
            try:
                result = await monitor.request('GET', f'/internal/v1/hosts/{ssh_id}')
            except MonitorHTTPError as error:
                return await failure('HOST_NOT_FOUND' if error.status in (403, 404) else error.code,
                                     error.status if error.status in (403, 404) else 503)
            except (MonitorUnavailable, TimeoutError):
                return await failure()
            host = host_shape(result.get('host', {}))
            connectivity = (host.get('connectivity') or {}).get('state', 'unknown')
            access = host.get('monitoring_access', 'disabled')
            sample = host.get('sample') or {}
            transport = bool(host.get('fingerprint_sha256')) and connectivity != 'unknown'
            capabilities = {
                'transport': transport,
                'monitoring': access == 'allowed',
                'execution': False,
                'workspace': False,
            }
            readiness = ('ready' if connectivity == 'online'
                         else 'degraded' if connectivity == 'stale'
                         else 'unavailable')
            return JSONResponse({
                'schema_version': 1,
                'computer': {
                    'host_id': ssh_id,
                    'display_name': host.get('display_name') or ssh_alias,
                    'method': 'ssh',
                    'connection': {
                        'method': 'ssh', 'alias': ssh_alias,
                        'host_key_fingerprint': host.get('fingerprint_sha256', ''),
                    },
                    'readiness': readiness,
                    'capabilities': capabilities,
                    'status': {
                        'connectivity': connectivity,
                        'monitoring_access': access,
                        'observed_at': sample.get('observed_at'),
                        'latency_ms': (host.get('connectivity') or {}).get('latency_ms'),
                        'stale_groups': host.get('stale_groups') or [],
                        'error': None,
                    },
                },
            }, headers={'Cache-Control': 'no-store'})

        ssh_computer.__annotations__['request'] = Request
        app.add_api_route('/v1/ssh/computer', ssh_computer, methods=['GET'])
