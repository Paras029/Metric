"""Cooperative cancellation for a stage in progress.

A stage runs on a background thread and makes anywhere from one to several hundred model calls.
Python cannot safely kill a thread mid network call, and does not need to: what "stop" has to
mean is that no further call is *started* once asked, not that an in-flight one is torn down. A
person who changes their mind should not sit through calls they no longer want, but a call
already sent is left to finish rather than abandoned -- that keeps the connection well-behaved
and, just as importantly, means nothing here ever writes a workspace file half-built from a run
that did not finish.

Every pass that makes more than one call accepts an optional ``threading.Event`` and checks it
between calls with :func:`check`, which raises :class:`Stopped` the moment it is set. The event is
optional throughout: a caller that passes nothing -- the command line, or a test -- checks nothing
and never stops early, which is why cancellation costs those callers no ceremony at all.
"""
from __future__ import annotations

import threading
from typing import Optional


class Stopped(Exception):
    """Raised to unwind a stage promptly once its cancellation signal has been set."""

    def __init__(self, message: str = "Stopped before finishing.") -> None:
        super().__init__(message)


def is_set(event: Optional[threading.Event]) -> bool:
    """Whether a stop has been requested. False where there is nothing to check."""
    return event is not None and event.is_set()


def check(event: Optional[threading.Event]) -> None:
    """Raise :class:`Stopped` if a stop has been requested; otherwise do nothing."""
    if is_set(event):
        raise Stopped()
