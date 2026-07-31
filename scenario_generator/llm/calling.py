"""Invoking a completion function that may or may not understand tiers, or batching.

Every pass in this package takes a ``complete`` callable so it can be driven by a stub in a test
or by an alternative gateway. The real gateway accepts a ``tier``; a two-argument stub does not.

The obvious way to bridge that is to try the call with the tier and catch ``TypeError``, and that
is what this replaces. The problem with catching is what else it catches: a ``TypeError`` raised
*inside* the gateway -- from a malformed payload, or a ``None`` where a string was expected --
looks identical to one raised by the call signature, and the retry then repeats the same failure
with fewer arguments and reports the second failure instead of the first. Asking the signature
what it accepts distinguishes the two, so a real error is raised as itself.

The same problem exists one level up, for batching. ``ask_llm`` in the real gateway carries a
``.batch`` attribute pointing at the concurrent version; a test stub is just a function and has
no such thing. :func:`call_batch` looks for it and runs several prompts at once where it is
there, and falls back to calling the plain function once per prompt -- through the same
tier-aware :func:`call` -- where it is not. A stub written before batching existed keeps working
unchanged and simply does not get the speed.
"""
from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable, List, Optional, Sequence, Union

from ..utils import parse_json_object

from .cancellation import Stopped, is_set

logger = logging.getLogger(__name__)


def parsed_reply(reply: Any, pass_name: str, subject: str) -> dict:
    """One entry from :func:`call_batch` as a parsed object, or an empty one, with the reason said.

    Every batched pass needs exactly this, and for the same reason: ``call_batch`` reports a
    failed call as the exception rather than raising it, so a chunk whose call never landed and a
    chunk whose reply would not parse arrive as two shapes with one meaning -- nothing usable
    came back for these ids. Both leave the chunk as the passes before it set it, which every
    caller already handles, because it is also what happens when a reply parses but leaves an id
    out.

    ``subject`` names the ids involved so the log line says which part of the benchmark went
    unanswered rather than only that something did.
    """
    if isinstance(reply, BaseException):
        logger.warning("%s left unchanged (%s): %s", pass_name, subject, reply)
        return {}
    try:
        return parse_json_object(reply)
    except Exception as exc:
        logger.warning("%s left unchanged (%s): %s", pass_name, subject, exc)
        return {}


def accepts(complete: Callable[..., str], name: str) -> bool:
    """Whether this callable takes a keyword argument of that name.

    A callable whose signature cannot be read -- a builtin, or something wrapped in C -- is
    assumed not to, which degrades to the plain two-argument call rather than failing.

    Cached where it can be. The answer is a property of the function and cannot change, and this
    is asked two or three times for every call every batched pass makes; building a ``Signature``
    is real reflection work to repeat several hundred times a run for a fixed answer. A callable
    that is not hashable -- an ordinary dataclass instance with a ``__call__``, say -- is answered
    directly instead of being refused.
    """
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
              tier: Optional[Any] = None, cancel=None,
              **extra: Any) -> List[Union[str, BaseException]]:
    """Call several prompts under one system prompt, concurrently where that is available.

    Returns one entry per message, in the same order, and never raises for an individual
    failure: an entry is either the reply text or the exception that call raised getting it,
    exactly as the real gateway's batch reports a partial failure. A caller already has to
    handle a chunk the model dropped from a JSON reply; this is the same handling, one level
    earlier, so a chunk that failed outright is not a different case to write.

    Real concurrency comes from a ``.batch`` attribute on ``complete`` -- the production gateway
    sets one, pointing at its own concurrent implementation. Nothing here assumes it exists:
    without it, this calls ``complete`` once per message through :func:`call`, which is what a
    test stub written before batching existed already does, unchanged.

    ``cancel``, if given, is a ``threading.Event`` that a caller can set to ask this to stop
    sending further messages. Anything not yet sent when it is noticed comes back as a
    :class:`~.cancellation.Stopped` entry rather than being sent -- handled by every caller
    exactly like any other failed chunk, and passed through to the real batch function where it
    understands it (the production gateway sends its concurrent waves in a loop of its own and
    checks between them) or checked here, once per message, where it does not.
    """
    if not user_messages:
        return []

    batch_fn = getattr(complete, "batch", None)
    if callable(batch_fn):
        keywords = {name: value for name, value in extra.items() if accepts(batch_fn, name)}
        if tier is not None and accepts(batch_fn, "tier"):
            keywords["tier"] = tier
        if cancel is not None and accepts(batch_fn, "cancel"):
            keywords["cancel"] = cancel
        return list(batch_fn(system_prompt, list(user_messages), **keywords))

    results: List[Union[str, BaseException]] = []
    for message in user_messages:
        if is_set(cancel):
            results.append(Stopped())
            continue
        try:
            results.append(call(complete, system_prompt, message, tier=tier, **extra))
        except Exception as exc:                          # reported, not raised -- see above
            results.append(exc)
    return results
