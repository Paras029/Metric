"""In-memory stop signals for stages running on a background thread.

A stage's work only ever happens inside this process, on the thread ``run_stage`` starts for it,
so the signal that stops it only ever needs to reach that thread -- there is nothing to persist.
Keeping it on disk alongside the rest of a workspace's state would in fact be wrong: a signal that
survived a restart would outlive the very thread it was meant to stop, and could be found "still
set" against a stage that was never actually asked to stop this time.

One event per (workspace, stage) that is currently running. :func:`start` is called from the
request that launches the background thread, before the thread exists, so a stop clicked in the
instant after that request returns is never lost waiting for the thread to register itself.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, Tuple

_events: Dict[Tuple[str, str], threading.Event] = {}
_guard = threading.Lock()


def _key(root: Path, stage_key: str) -> Tuple[str, str]:
    return (str(Path(root).resolve()), stage_key)


def start(root: Path, stage_key: str) -> threading.Event:
    """A fresh stop signal for a stage about to run, replacing any left over from a past run."""
    event = threading.Event()
    with _guard:
        _events[_key(root, stage_key)] = event
    return event


def stop(root: Path, stage_key: str) -> bool:
    """Ask a running stage to stop. Returns whether anything was listening."""
    with _guard:
        event = _events.get(_key(root, stage_key))
    if event is None:
        return False
    event.set()
    return True


def clear(root: Path, stage_key: str, event: threading.Event = None) -> None:
    """Drop a stage's signal once its run has ended, however it ended.

    Pass the event that run was given. A stage that fails is marked failed *before* its thread
    reaches this, which leaves a window in which the stage reads as runnable again and a second
    run can register a signal of its own -- and clearing by key alone would then throw away the
    new run's signal instead of the old one's, leaving a stage that is genuinely running with
    nothing listening for a stop. Clearing only what this run registered cannot do that.
    """
    key = _key(root, stage_key)
    with _guard:
        if event is None or _events.get(key) is event:
            _events.pop(key, None)
