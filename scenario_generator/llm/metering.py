"""Counting the model calls a stage makes.

Reading a document pack or reviewing a large benchmark is anywhere from three calls to a hundred,
and until a run finishes there is nothing that says which. The number is worth reporting for two
reasons that have nothing to do with curiosity: it is what a gateway quota is spent in, and a
count that jumps between two runs of the same stage is usually the first visible sign that
batching, chunking or the refill path has changed behaviour.

Counted in :mod:`scenario_generator.llm.gateway`, at the point a request is actually sent, so a
pass driven by a test stub counts nothing -- there was no model call to count. A batch counts once
per message in it, since that is what reaches the gateway. Retries inside a call are not counted
again: the interesting number is how much work a stage asked for, not how many times the transport
had to ask for it.

The counter is process-wide rather than passed down through every pass's signature, because it has
to survive the thread pools ingestion reads its facet groups on -- a context-local would be
invisible inside those workers. Both front ends run one stage at a time, so a window around one
stage measures that stage.
"""
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
    """Measure the calls made inside this block.

    Yields a callable rather than a number, so it reads correctly whether it is asked during the
    block or after it::

        with counted() as calls:
            run_the_stage()
        logger.info("%d model call(s).", calls())
    """
    start = calls_made()
    yield lambda: calls_made() - start
