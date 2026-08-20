"""Deterministic checks on what a generative pass produced, and what to say when it failed."""
from __future__ import annotations

import re
from typing import List, Sequence

from metric.domain.models import turn_plan_lines
from metric.domain.models import Scenario

# Words too common to make a phrase distinctive. Not a general stopword list -- only the ones that
# actually appear in the endings this compares against, where "the account is locked" and "the
# account is verified" would otherwise share two thirds of a three-word run.
_COMMON = frozenset("""
a an and are as at be been being but by for from has have in into is it its of on or over that
the their then there they this to was were what when where which who will with without you your
""".split())

_WORD = re.compile(r"[a-z][a-z'-]*")

# How many consecutive distinctive words have to appear in both texts before this calls it a
# repetition of the ending. Two is too easy -- "dispute filed" turns up honestly in a description
# of somebody filing a dispute. Four almost never fires. Three is the width at which a match is
# a phrase somebody carried across rather than a coincidence of subject matter.
SHINGLE = 3

# Below this a description is not a description. The prompt asks for two or three sentences; this
# is the floor at which what came back is a fragment rather than a short answer.
MIN_DESCRIPTION_CHARS = 40

# A numbered line shorter than this is not an instruction. "Continue the conversation." and
# "Confirm." are lines a tester cannot act on, and they are what a plan degrades into when the
# writer has run out of things to say but still owes a line.
MIN_TURN_WORDS = 5

# Words that stand in for the thing they should have named. A turn plan exists to be executed by
# somebody who has never seen this agent, and "provide the relevant details" leaves them to invent
# the test -- at which point two testers run two different tests and the transcripts cannot be
# compared, which is the failure this whole exercise is built to avoid.
_VAGUE = (
    "relevant details", "relevant information", "appropriate details", "appropriate information",
    "necessary details", "necessary information", "required details", "required information",
    "as needed", "as appropriate", "as necessary", "if necessary", "if required",
    "some kind of", "or similar", "and so on", "etc.", "etc ",
    "provide details", "give details", "supply details",
    "continue the conversation", "proceed as normal", "respond accordingly",
)

# Text the writer left for somebody else to fill in.
_PLACEHOLDER = re.compile(r"\[(?:insert|add|tbd|todo|placeholder)[^\]]*\]|<[a-z_ ]+>|\bTBD\b|\bXXX\b",
                          re.I)


def _distinctive(text: str) -> List[str]:
    """The content words of a string, in order, with the common ones dropped."""
    return [word for word in _WORD.findall((text or "").lower()) if word not in _COMMON]


def _shingles(words: Sequence[str], width: int = SHINGLE) -> set:
    return {tuple(words[i:i + width]) for i in range(len(words) - width + 1)}


def repeats_the_ending(text: str, ending: str) -> bool:
    """Whether ``text`` carries a distinctive phrase out of ``ending``."""
    if not text or not ending:
        return False
    return bool(_shingles(_distinctive(text)) & _shingles(_distinctive(ending)))


def _vague_in(text: str) -> str:
    """The first stand-in phrase in a turn plan, or an empty string."""
    lowered = " " + " ".join((text or "").lower().split()) + " "
    return next((phrase for phrase in _VAGUE if phrase in lowered), "")


def problems(scenario: Scenario) -> List[str]:
    """What is wrong with this scenario's written text, phrased for the model to act on."""
    found: List[str] = []

    if not (scenario.name or "").strip():
        found.append("name is missing.")

    description = (scenario.description or "").strip()
    if not description:
        found.append("description is missing.")
    elif len(description) < MIN_DESCRIPTION_CHARS:
        found.append(f"description is only {len(description)} characters -- it needs to say what "
                     f"situation the tester is setting up, in two or three sentences.")

    lines = [line for line in turn_plan_lines(scenario) if line.strip()]
    # The same number the writer was given, which is not the same question for the two kinds. A
    # route is scripted turn by turn and the count is exact; a probe has no decision path, so its
    # count is a floor and the prompt says so. Checking a probe against the route's number would
    # report a plan the prompt asked for.
    wanted = (len(scenario.turn_meta) if getattr(scenario, "is_probe", False)
              else getattr(scenario, "turn_count", 0) or 0)
    if not (scenario.turn_plan or "").strip():
        found.append("turn_plan is missing.")
    elif len(lines) < 1:
        found.append("turn_plan has no numbered lines -- each turn needs its own, "
                     "written \"1. \", \"2. \" and so on.")
    elif wanted and len(lines) < wanted:
        # Short, not long. A plan with more lines than turns has said too much, which is a style
        # problem; a plan with fewer has left part of the route with no instruction against it,
        # and the tester cannot reach the situation the scenario is about.
        found.append(f"turn_plan has {len(lines)} numbered lines where the route needs {wanted}. "
                     f"Every step the tester drives needs its own line, or the route cannot be "
                     f"reached.")

    for index, line in enumerate(lines, start=1):
        if len(_WORD.findall(line.lower())) < MIN_TURN_WORDS:
            found.append(f"turn_plan line {index} is too short to act on. Say what the tester "
                         f"says or supplies, in the terms a real user would use.")
            break

    vague = _vague_in(scenario.turn_plan)
    if vague:
        found.append(f"turn_plan says \"{vague}\" instead of naming what the tester actually "
                     f"provides. A tester who has never seen this agent has to be able to run it "
                     f"without inventing the missing half.")

    for field, text in (("description", description), ("turn_plan", scenario.turn_plan or "")):
        left = _PLACEHOLDER.search(text)
        if left:
            found.append(f"{field} still carries \"{left.group(0)}\", which is text left for "
                         f"somebody else to fill in. Write the real subject matter.")

    # The answer-key check, last because it is the one worth reading first when it fires.
    ending = scenario.termination or ""
    if repeats_the_ending(description, ending):
        found.append("description states how the interaction ends. Say what the tester sets up "
                     "and what makes this route distinct; never what the agent does about it, and "
                     "never how it turns out.")
    if repeats_the_ending(scenario.turn_plan, ending):
        found.append("turn_plan states how the interaction ends. Each line tells the tester what "
                     "to say or do -- it never says what the agent will do in reply.")

    return found


def leaking(scenarios: Sequence[Scenario]) -> List[Scenario]:
    """Only the scenarios whose text gives the ending away. For reporting, after any repair."""
    return [s for s in scenarios
            if repeats_the_ending(s.description, s.termination)
            or repeats_the_ending(s.turn_plan, s.termination)]
