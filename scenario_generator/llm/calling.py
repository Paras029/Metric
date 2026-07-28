"""Invoking a completion function that may or may not understand tiers.

Every pass in this package takes a ``complete`` callable so it can be driven by a stub in a test
or by an alternative gateway. The real gateway accepts a ``tier``; a two-argument stub does not.

The obvious way to bridge that is to try the call with the tier and catch ``TypeError``, and that
is what this replaces. The problem with catching is what else it catches: a ``TypeError`` raised
*inside* the gateway -- from a malformed payload, or a ``None`` where a string was expected --
looks identical to one raised by the call signature, and the retry then repeats the same failure
with fewer arguments and reports the second failure instead of the first. Asking the signature
what it accepts distinguishes the two, so a real error is raised as itself.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional


def accepts(complete: Callable[..., str], name: str) -> bool:
    """Whether this callable takes a keyword argument of that name.

    A callable whose signature cannot be read -- a builtin, or something wrapped in C -- is
    assumed not to, which degrades to the plain two-argument call rather than failing.
    """
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
