"""Loading and rendering the prompt library.

Prompt text lives in ``prompts/`` beside the step that sends it, one plain file per prompt, so
wording can be changed without touching Python. This module is the only place they are read.

Placeholders are ``{{name}}`` rather than :meth:`str.format`, because prompts embed JSON examples
and every literal brace in those would otherwise need doubling. Rendering is strict both ways: a
placeholder the caller did not supply, and a value with no slot, are both errors that name the
file.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set

PROMPT_SUFFIX = ".md"
_ROOT = Path(__file__).resolve().parent.parent

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class PromptError(RuntimeError):
    """A prompt file is missing or unreadable, or was rendered with the wrong placeholders."""


@lru_cache(maxsize=1)
def _index() -> Dict[str, Path]:
    """Every prompt in the package, by name. Names carry the step they belong to, so they stay
    unique across the directories."""
    return {path.stem: path for path in sorted(_ROOT.rglob(f"prompts/*{PROMPT_SUFFIX}"))}


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """The raw text of a prompt, cached for the life of the process. An edit therefore takes
    effect on the next run, not mid-run."""
    path = _index().get(name)
    if path is None:
        raise PromptError(f"no prompt named '{name}' under {_ROOT}")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptError(f"cannot read prompt '{name}' at {path}: {exc}") from exc


def placeholders(name: str) -> Set[str]:
    """The placeholder names a prompt expects to be given."""
    return set(_PLACEHOLDER.findall(load(name)))


def render(name: str, **values: object) -> str:
    """Fill a prompt's placeholders, rejecting any mismatch between template and caller."""
    template = load(name)
    required = set(_PLACEHOLDER.findall(template))
    supplied = set(values)

    missing = sorted(required - supplied)
    unused = sorted(supplied - required)
    if missing or unused:
        problems = []
        if missing:
            problems.append(f"no value given for {', '.join(missing)}")
        if unused:
            problems.append(f"no {{{{...}}}} slot for {', '.join(unused)}")
        raise PromptError(f"prompt '{name}': " + "; ".join(problems))

    # A function replacement keeps backslashes in the substituted text literal, which matters
    # because scenario descriptions and turn plans routinely contain them.
    return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), template)


def available() -> List[str]:
    """Every prompt name in the library, sorted."""
    return sorted(_index())
