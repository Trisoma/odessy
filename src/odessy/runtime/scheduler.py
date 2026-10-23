"""Bounded tool dispatch; authority stays in the serial prepare callback."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from odessy.runtime.types import ToolBatch, ToolCall, ToolObservation

SAFE_TOOLS = frozenset({'time.now', 'calculator.eval', 'filesystem.read', 'filesystem.list', 'project.find', 'project.grep'})


def concurrency_from_env() -> int:
    raw = os.environ.get('ODESSY_TOOL_CONCURRENCY', '1')
    try:
        limit = int(raw)
    except ValueError:
        raise ValueError('ODESSY_TOOL_CONCURRENCY must be an integer from 1 to 8') from None
    if not 1 <= limit <= 8:
        raise ValueError('ODESSY_TOOL_CONCURRENCY must be an integer from 1 to 8')
    return limit


def is_concurrency_safe(request) -> bool:
    return request.name in SAFE_TOOLS


async def schedule_calls(
    batch: ToolBatch, *, limit: int,
    safe: Callable[[ToolCall], bool],
    prepare: Callable[[ToolCall], Awaitable[dict[str, Any]]],
    dispatch: Callable[[ToolCall, dict[str, Any]], Awaitable[dict[str, Any]]],
    stop: asyncio.Event | None = None,
    drain_seconds: float = 30,
) -> tuple[list[ToolObservation], str | None]:
    """Exclusive calls are barriers. Only dispatch overlaps, results stay ordered."""
    if not 1 <= limit <= 8:
        raise ValueError('concurrency must be from 1 to 8')
    running: dict[asyncio.Task, int] = {}
    results: dict[int, dict[str, Any]] = {}
    next_index = 0
    reason = None
    watcher = asyncio.create_task(stop.wait()) if stop is not None else None

    async def collect(*, one: bool = False) -> None:
        nonlocal reason
        if not running:
            return
        waiting = set(running)
        if watcher is not None:
            waiting.add(watcher)
        done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        for task in done.intersection(running):
            index = running.pop(task)
            try:
                results[index] = task.result()
            except PermissionError as exc:
                results[index] = {'status': 'denied', 'reason': str(exc)}
                reason = str(exc)
            except Exception as exc:
                results[index] = {'status': 'error', 'error': str(exc)}
        if not one and running and not (stop is not None and stop.is_set()):
            await collect()

    async def drain() -> None:
        if not running:
            return
        done, pending = await asyncio.wait(set(running), timeout=drain_seconds)
        for task in done:
            index = running.pop(task)
            try:
                results[index] = task.result()
            except Exception as exc:
                results[index] = {'status': 'error', 'error': str(exc)}
        for task in pending:
            results[running.pop(task)] = {'status': 'unknown', 'reason': 'Client wait stopped; remote completion is unknown'}
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    try:
        while next_index < len(batch.calls):
            if reason is not None:
                break
            if stop is not None and stop.is_set():
                reason = 'stopped by user'
                break
            call = batch.calls[next_index]
            try:
                parallel = safe(call) is True
            except Exception:
                parallel = False
            if not parallel:
                await collect()
            elif len(running) >= limit:
                await collect(one=True)
            if stop is not None and stop.is_set():
                reason = 'stopped by user'
                break
            if reason is not None:
                break
            # Admission may have changed while a pool slot was awaited.
            try:
                still_parallel = safe(call) is True
            except Exception:
                still_parallel = False
            if parallel and not still_parallel:
                await collect()
                parallel = False
            if reason is not None:
                break
            if stop is not None and stop.is_set():
                reason = 'stopped by user'
                break
            try:
                args = await prepare(call)
            except PermissionError as exc:
                results[next_index] = {'status': 'denied', 'reason': str(exc)}
                next_index += 1
                reason = str(exc)
                break
            if stop is not None and stop.is_set():
                reason = 'stopped by user'
                break
            running[asyncio.create_task(dispatch(call, args))] = next_index
            next_index += 1
            if not parallel:
                await collect()
        if reason is None:
            await collect()
            if stop is not None and stop.is_set():
                reason = 'stopped by user'
        if reason is not None:
            await drain()
    except asyncio.CancelledError:
        reason = 'turn cancelled'
        await asyncio.shield(drain())
    finally:
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        if running:
            await asyncio.shield(drain())
    observations = [ToolObservation(call.request, results.get(i, {'status': 'skipped', 'reason': reason or 'not dispatched'}), call.call_id) for i, call in enumerate(batch.calls)]
    return observations, reason
