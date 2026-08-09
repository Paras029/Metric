"""Running several stages in one go, without any of them behaving differently for it.

The pipeline is seven stages and most of them take minutes. Sitting in front of it clicking Run,
waiting, clicking Run again is the largest cost the interface imposes on someone who already knows
what they want, and it is a cost with nothing to show for it: the order is fixed, the dependencies
are known, and there is no decision to make between the benchmark being built and its text being
written.

So a plan is simply a list of stage keys run back to back on one thread. Every stage still runs
exactly as it does on its own -- the same runner, the same status, the same progress, the same
stop signal -- and this module only decides which stages go into the list and stops the list when
one of them does not finish. Nothing here knows what any stage does.

Two rules the plan follows, both about not doing something the person did not ask for:

**A stage that is already complete is not re-run.** Running through to the review after correcting
the intake should re-run what the correction invalidated, not spend an hour re-writing text that
is still current. Anything genuinely out of date is marked stale rather than complete, so it is
picked up.

**An optional stage with nothing to work on is skipped, not failed.** Most engagements submit no
transcripts from the model owner, and a plan that stopped dead at the coverage stage every time --
or worse, marked it failed -- would make the whole control useless for the common case.
"""
from __future__ import annotations

from typing import List

from .runners import RUNNERS, has_input
from .stages import COMPLETE, RUNNING, STAGE_BY_KEY, STAGE_KEYS, index_of
from .workspace import Workspace


def plan(workspace: Workspace, through: str) -> List[str]:
    """The stages a "run through to X" would actually run, in order.

    Blocked stages are included rather than filtered out: a stage is blocked because the stage
    before it has not produced anything yet, and running that one first is the entire point. What
    is excluded is work there is no reason to redo, and optional stages nothing was submitted for.
    """
    if through not in STAGE_BY_KEY:
        return []
    keys = []
    for key in STAGE_KEYS[:index_of(through) + 1]:
        if key not in RUNNERS:
            continue
        state = workspace.state(key)
        if state.status in (COMPLETE, RUNNING):
            continue
        if STAGE_BY_KEY[key].optional and not has_input(workspace, key):
            continue
        keys.append(key)
    return keys


def targets(workspace: Workspace) -> List[dict]:
    """The stages worth offering as an end point, each with what running through to it would cost.

    A stage nothing would run for is still offered, marked as such, rather than hidden: a control
    whose options come and go as the pipeline advances is harder to learn than one whose options
    are fixed and honest about what each would do right now.
    """
    rows = []
    for stage in (STAGE_BY_KEY[key] for key in STAGE_KEYS):
        if stage.key not in RUNNERS:
            continue
        steps = plan(workspace, stage.key)
        rows.append({"key": stage.key, "title": stage.title, "steps": len(steps)})
    return rows


def describe(keys: List[str]) -> str:
    """The plan as a sentence, for the page to show before anything is started."""
    titles = [STAGE_BY_KEY[key].title for key in keys]
    if not titles:
        return "nothing left to run"
    if len(titles) == 1:
        return titles[0]
    return ", ".join(titles[:-1]) + " and " + titles[-1]
