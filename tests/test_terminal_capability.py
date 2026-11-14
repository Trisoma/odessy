"""B13 gate: the terminal capability is honestly unavailable, never simulated."""
from __future__ import annotations

import asyncio

import pytest

from odessy.application.terminals import (
    PTY_REASON,
    PtyUnavailable,
    TerminalService,
    pty_availability,
    register,
)


def test_capability_reports_the_missing_dependency_and_no_remote_terminal():
    state = pty_availability()
    assert state['available'] is False
    assert 'no reviewed PTY dependency' in state['reason']
    assert state['recorded_output_is_not_terminal'] is True
    assert state['remote_terminal'] is False
    assert 'node-pty' in state['dependency']
    assert state['reason'] == PTY_REASON


@pytest.mark.parametrize('operation', [
    lambda service: service.create('s1'),
    lambda service: service.output('s1', 'term_1'),
    lambda service: service.input('s1', 'term_1', text='ls\n', expected_revision=1),
    lambda service: service.resize('s1', 'term_1', columns=100, rows=40,
                                   expected_revision=1),
    lambda service: service.interrupt('s1', 'term_1', expected_revision=1),
    lambda service: service.close('s1', 'term_1', expected_revision=1),
])
def test_every_terminal_operation_fails_closed(operation):
    service = TerminalService()
    assert service.available is False
    with pytest.raises(PtyUnavailable) as refused:
        operation(service)
    assert refused.value.status == 501
    assert 'Recorded command output is not a terminal' in str(refused.value)


def test_close_session_owns_no_process():
    service = TerminalService()
    assert service.close_session('s1') is None


def test_registrar_mounts_no_terminal_route():
    pytest.importorskip('fastapi')
    from odessy.application.api import create_app

    def registrar(app, dependencies):
        register(app, dependencies)

    app = create_app(lambda: (_ for _ in ()).throw(AssertionError('no engine')),
                     token='fixture-token')
    registrar(app, None)
    paths = {getattr(route, 'path', '') for route in app.routes}
    assert not any('/terminals' in path for path in paths)
    assert isinstance(app.state.terminals, TerminalService)


def test_capability_payload_is_stable_json():
    import json

    first = pty_availability()
    assert json.loads(json.dumps(first)) == first
    assert set(first) == {'available', 'reason', 'dependency',
                          'recorded_output_is_not_terminal', 'remote_terminal'}
