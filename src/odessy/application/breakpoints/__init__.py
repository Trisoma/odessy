"""Execution breakpoints and task-level undo over durable conversations.

Breakpoints are append-only ``kind='breakpoint'`` events that record *what was in
flight* at a point during a turn, so a failed task can be undone back to a prior
anchor. They are observation data, never an authorization source: rollback still
goes through the existing broker and workspace authority.

Public seam used by the integrator::

    from odessy.application.breakpoints import (
        BreakpointService, build_service, is_critical, register)

Nothing here starts a model, spawns a process, or writes workspace files until an
explicit, approved undo is requested.
"""
from __future__ import annotations

from .policy import is_critical
from .service import BreakpointHook, BreakpointService, Unsupported, build_service
from .routes import create_registrar, register

__all__ = [
    'BreakpointService', 'BreakpointHook', 'build_service', 'is_critical', 'Unsupported',
    'create_registrar', 'register',
]
