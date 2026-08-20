"""In-memory stop signals for stages running on a background thread."""
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
    """Drop a stage's signal once its run has ended, however it ended."""
    key = _key(root, stage_key)
    with _guard:
        if event is None or _events.get(key) is event:
            _events.pop(key, None)
