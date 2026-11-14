import asyncio
import pytest
from odessy.authority.tool_control import ToolControl, ToolClient

def test_consent_and_stop_share_a_session_but_not_mcp_stdio():
    async def run():
        asked = asyncio.Event()
        async def approve(details):
            asked.set()
            return True, 'approved'
        control = ToolControl(asyncio.get_running_loop(), approve)
        client = ToolClient(*control.address)
        try:
            control.begin()
            assert await asyncio.to_thread(client.consent, {'operation': 'write'}) == (True, 'approved')
            assert asked.is_set()
            control.cancel()
            assert await asyncio.to_thread(client.consent, {'operation': 'write'}) == (False, 'cancelled')
        finally:
            control.close()

    asyncio.run(run())
def test_stop_retracts_a_pending_approval():
    async def run():
        entered = asyncio.Event()
        async def approve(details):
            entered.set()
            await asyncio.Event().wait()
        control = ToolControl(asyncio.get_running_loop(), approve)
        client = ToolClient(*control.address)
        try:
            control.begin()
            result = asyncio.create_task(asyncio.to_thread(client.consent, {}))
            await asyncio.wait_for(entered.wait(), 2)
            control.cancel()
            assert await asyncio.wait_for(result, 2) == (False, 'cancelled')
        finally:
            control.close()
    asyncio.run(run())

def test_stop_terminates_a_real_command_and_other_session_stays_live():
    import subprocess
    import sys
    async def run():
        async def approve(details):
            return True, 'approved'
        first = ToolControl(asyncio.get_running_loop(), approve)
        second = ToolControl(asyncio.get_running_loop(), approve)
        first.begin(); second.begin()
        client = ToolClient(*first.address)
        process = None
        try:
            assert client.enter()
            process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                       start_new_session=True)
            client.on_spawn(process)
            first.cancel()
            await asyncio.to_thread(process.wait, 5)
            assert process.poll() is not None
            other = ToolClient(*second.address)
            assert await asyncio.to_thread(other.consent, {}) == (True, 'approved')
        finally:
            if process is not None and process.poll() is None:
                process.kill()
            first.close(); second.close()
    asyncio.run(run())

def test_malformed_control_frames_fail_closed_without_handler_errors():
    import socket
    import json
    async def run():
        called = []
        async def approve(details):
            called.append(details)
            return True, 'approved'
        control = ToolControl(asyncio.get_running_loop(), approve)
        try:
            control.begin()
            for value in ([], None, {'token': 'wrong', 'kind': 'state'},
                          {'token': control.token, 'kind': 'spawn', 'pid': -1}):
                with socket.create_connection(('127.0.0.1', control.address[0]), timeout=1) as sock:
                    sock.sendall(json.dumps(value).encode() + b'\n')
                    sock.shutdown(socket.SHUT_WR)
                    data = sock.recv(4096)
                    if data:
                        assert json.loads(data).get('ok') is not True
            assert not called
            assert not control.running
        finally:
            control.close()
    asyncio.run(run())


def test_new_turn_is_refused_until_old_tool_cleanup_is_acknowledged():
    async def run():
        async def approve(details):
            return True, 'approved'
        control = ToolControl(asyncio.get_running_loop(), approve)
        client = ToolClient(*control.address)
        try:
            control.begin()
            assert client.enter()
            client.request(kind='spawn', pid=12345, epoch=client.local.epoch)
            control.cancel()
            with pytest.raises(RuntimeError, match='cleanup'):
                control.begin()
            client.request(kind='finished', pid=12345, epoch=client.local.epoch)
            control.begin()
        finally:
            control.close()
    asyncio.run(run())


def test_spawn_preflight_rejects_a_stale_approved_generation():
    async def run():
        async def approve(details):
            return True, 'approved'
        control = ToolControl(asyncio.get_running_loop(), approve)
        client = ToolClient(*control.address)
        try:
            control.begin()
            assert await asyncio.to_thread(client.consent, {}) == (True, 'approved')
            # enter on the actual tool thread; consent above used another executor thread.
            assert client.enter()
            control.cancel()
            with pytest.raises(Exception, match='cancelled|unavailable'):
                client.before_spawn()
        finally:
            control.close()
    asyncio.run(run())

def test_close_disposes_the_control_listener_and_revokes_approvals():
    import socket
    async def run():
        async def approve(details):
            return True, 'approved'
        control = ToolControl(asyncio.get_running_loop(), approve)
        port, token = control.address
        control.begin()
        await asyncio.to_thread(control.close)
        with pytest.raises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=.2)
        with pytest.raises(RuntimeError):
            control.begin()
    asyncio.run(run())
