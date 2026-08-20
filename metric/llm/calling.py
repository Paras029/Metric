"""Invoking a completion function that may or may not understand tiers, or batching."""
from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable, List, Optional, Sequence, Union

from metric.shared import parse_json_object

from metric.llm.cancellation import Stopped, is_set

logger = logging.getLogger(__name__)


def parsed_reply(reply: Any, pass_name: str, subject: str) -> dict:
    """One entry from :func:`call_batch` as a parsed object, or an empty one, with the reason said."""
    if isinstance(reply, BaseException):
        logger.warning("%s left unchanged (%s): %s", pass_name, subject, reply)
        return {}
    try:
        return parse_json_object(reply)
    except Exception as exc:
        logger.warning("%s left unchanged (%s): %s", pass_name, subject, exc)
        return {}


def accepts(complete: Callable[..., str], name: str) -> bool:
    """Whether this callable takes a keyword argument of that name."""
    try:
        return _accepts_cached(complete, name)
    except TypeError:                                      # unhashable callable
        return _accepts(complete, name)


@functools.lru_cache(maxsize=256)
def _accepts_cached(complete: Callable[..., str], name: str) -> bool:
    return _accepts(complete, name)


def _accepts(complete: Callable[..., str], name: str) -> bool:
    try:
        signature = inspect.signature(complete)
    except (TypeError, ValueError):
        return False

    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name and parameter.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            return True
    return False


def call(complete: Callable[..., str], system_prompt: str, user_message: str,
         tier: Optional[Any] = None, **extra: Any) -> str:
    """Call a completion function, passing the tier only where it can accept one."""
    keywords = {name: value for name, value in extra.items() if accepts(complete, name)}
    if tier is not None and accepts(complete, "tier"):
        keywords["tier"] = tier
    return complete(system_prompt, user_message, **keywords)


def call_batch(complete: Callable[..., str], system_prompt: str, user_messages: Sequence[str],
              tier: Optional[Any] = None, cancel=None, on_progress=None,
              **extra: Any) -> List[Union[str, BaseException]]:
    """Call several prompts under one system prompt, concurrently where that is available."""
    if not user_messages:
        return []

    batch_fn = getattr(complete, "batch", None)
    if callable(batch_fn):
        keywords = {name: value for name, value in extra.items() if accepts(batch_fn, name)}
        if tier is not None and accepts(batch_fn, "tier"):
            keywords["tier"] = tier
        if cancel is not None and accepts(batch_fn, "cancel"):
            keywords["cancel"] = cancel
        if on_progress is not None and accepts(batch_fn, "on_progress"):
            keywords["on_progress"] = on_progress
        replies = list(batch_fn(system_prompt, list(user_messages), **keywords))
        # Reported again at the end regardless of whether it was passed through. A batch function
        # that took the argument may still have ignored it -- anything with ``**kwargs`` accepts
        # every name and honours none of them -- and a pass that reported nothing at all is the
        # one outcome worth ruling out. Repeating a count already reported costs nothing, since
        # progress only ever moves forward.
        if on_progress is not None:
            on_progress(len(replies), len(user_messages))
        return replies

    results: List[Union[str, BaseException]] = []
    for message in user_messages:
        if is_set(cancel):
            results.append(Stopped())
            continue
        try:
            results.append(call(complete, system_prompt, message, tier=tier, **extra))
        except Exception as exc:                          # reported, not raised -- see above
            results.append(exc)
        if on_progress is not None:
            on_progress(len(results), len(user_messages))
    return results
