"""Loading and rendering of the prompt library.

Prompt text lives in ``scenario_generator/prompts`` as one plain file per prompt, so wording can
be changed without touching Python. This module is the only place those files are read.

Placeholders use a doubled-brace form -- ``{{name}}`` -- rather than :meth:`str.format`. Prompts
embed JSON examples, and under ``str.format`` every literal brace in those examples would need to
be doubled, which makes the files hostile to anyone editing the wording. Here only ``{{word}}``
is substituted and every other brace is left exactly as written.

Rendering is strict in both directions: a placeholder the caller did not supply, and a value the
template has no slot for, are both errors. A rename on one side of the boundary therefore fails
immediately and names the file, rather than silently producing a malformed prompt that is only
noticed in the model's output.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Set

PROMPT_SUFFIX = ".md"
PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class PromptError(RuntimeError):
    """A prompt file is missing or unreadable, or was rendered with the wrong placeholders."""


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """Return the raw text of a prompt, cached for the life of the process.

    Because of that cache, an edit to a prompt file takes effect on the next run rather than
    mid-run. Prompts are read once and reused across every batch of a pass.
    """
    path = PROMPT_DIR / f"{name}{PROMPT_SUFFIX}"
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
    """Every prompt name in the library, sorted. Used by the tests that guard the library."""
    return sorted(path.stem for path in PROMPT_DIR.glob(f"*{PROMPT_SUFFIX}"))
