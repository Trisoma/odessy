"""A request-local guard shared by router children, including adapter repairs."""
from contextvars import ContextVar
from typing import Callable

generation_guard: ContextVar[Callable[[], None] | None] = ContextVar("odessy_generation_guard", default=None)


def before_generation() -> None:
    guard = generation_guard.get()
    if guard is not None:
        guard()
