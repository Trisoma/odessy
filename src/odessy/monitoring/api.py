"""Read-only routes behind the existing application authentication middleware."""
import asyncio
import re
from .supervisor import MonitorUnavailable, MonitorHTTPError


def register(app, monitor):
    from fastapi import Request
    from fastapi.responses import JSONResponse, StreamingResponse

    async def failure(code='MONITOR_UNAVAILABLE', status=503):
        return JSONResponse({'schema_version': 1, 'error': {'code': code, 'message': code, 'retryable': status == 503}}, status_code=status)

    async def serve(request):
        if monitor is None:
            return await failure()
        suffix = request.url.path.removeprefix('/v1/monitoring')
        internal = '/internal/v1' + (suffix or '/monitoring')
        if suffix.startswith('/hosts/') and not re.fullmatch(r'/hosts/host_[A-Za-z0-9_]{1,128}(?:/(metrics|history))?', suffix):
            return await failure('HOST_NOT_FOUND', 404)
        allowed = {'limit', 'cursor'} if suffix == '/hosts' else {'from', 'to', 'step', 'series'} if suffix.endswith('/history') else set()
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
                return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-store'})
            result = await monitor.request('GET', internal, dict(request.query_params))
            def host_shape(host):
                if not isinstance(host, dict):
                    return host
                host = dict(host)
                if isinstance(host.get('trust'), str):
                    host['trust'] = {'state': host['trust'], 'local_revision': host.get('generation', '').split(':', 1)[0] or None}
                if isinstance(host.get('connectivity'), str):
                    host['connectivity'] = {'state': host['connectivity'], 'last_seen_at': host.get('last_seen_at'), 'latency_ms': host.get('latency_ms')}
                return host
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

    # Avoid postponed Request annotations: FastAPI resolves these module globals.
    serve.__annotations__['request'] = Request
    for route in ('', '/hosts', '/hosts/{host_id}', '/hosts/{host_id}/metrics', '/hosts/{host_id}/history', '/events'):
        app.add_api_route('/v1/monitoring' + route, serve, methods=['GET'])
