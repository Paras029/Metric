"""Counting the model calls a stage makes."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator

_calls = 0
_lock = threading.Lock()


def record_call(count: int = 1) -> None:
    """Note that ``count`` request(s) have gone to the gateway."""
    global _calls
    with _lock:
        _calls += count


def calls_made() -> int:
    """Every call this process has sent since it started."""
    with _lock:
        return _calls


@contextmanager
def counted() -> Iterator[Callable[[], int]]:
    """Measure the calls made inside this block."""
    start = calls_made()
    yield lambda: calls_made() - start
