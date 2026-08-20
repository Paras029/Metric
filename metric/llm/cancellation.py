"""Cooperative cancellation for a stage in progress."""
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
