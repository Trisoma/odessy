"""Opt-in authenticated application routes; 07 supplies the reviewed live owner."""
from fastapi import HTTPException, Request
from fastapi.responses import Response

from .service import TerminalError


def register_local(app, dependencies, *, service):
    app.state.terminals = service

    async def invoke(method, *args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except TerminalError as error:
            raise HTTPException(error.status, detail={'code': 'terminal_error',
                                                      'message': error.message}) from None

    async def body(request, fields):
        try:
            value = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, 'Expected a JSON object') from None
        if not isinstance(value, dict) or set(value) != set(fields):
            raise HTTPException(400, 'Invalid terminal request fields')
        return value

    @app.post('/v1/sessions/{sid}/terminals', status_code=201)
    async def create(sid: str, request: Request):
        value = await body(request, {'columns', 'rows'})
        return await invoke(service.create, sid, **value)

    @app.get('/v1/sessions/{sid}/terminals/{tid}/output')
    async def output(sid: str, tid: str, after: int = 0):
        return await invoke(service.output, sid, tid, after=after)

    @app.post('/v1/sessions/{sid}/terminals/{tid}/input', status_code=204)
    async def input_text(sid: str, tid: str, request: Request):
        value = await body(request, {'text', 'expected_revision'})
        await invoke(service.input, sid, tid, **value)
        return Response(status_code=204)

    @app.put('/v1/sessions/{sid}/terminals/{tid}/size')
    async def resize(sid: str, tid: str, request: Request):
        value = await body(request, {'columns', 'rows', 'expected_revision'})
        return await invoke(service.resize, sid, tid, **value)

    @app.post('/v1/sessions/{sid}/terminals/{tid}/interrupt')
    async def interrupt(sid: str, tid: str, request: Request):
        value = await body(request, {'expected_revision'})
        return await invoke(service.interrupt, sid, tid, **value)

    @app.delete('/v1/sessions/{sid}/terminals/{tid}')
    async def close(sid: str, tid: str, request: Request):
        value = await body(request, {'expected_revision'})
        return await invoke(service.close, sid, tid, **value)
