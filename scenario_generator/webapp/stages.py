"""The stages a use case moves through, in the order they run.

Each stage's own page says what it does and how; the list here is the order, the dependencies
between them, and which ones can be skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# Stage status. A stage that has run and then had its inputs changed underneath it is stale
# rather than complete: its output still exists and can still be read, but it no longer reflects
# what it was built from, and saying so is the whole reason this state exists.
LOCKED = "locked"
READY = "ready"
RUNNING = "running"
COMPLETE = "complete"
STALE = "stale"
FAILED = "failed"
STOPPED = "stopped"

STATUS_LABELS = {
    LOCKED: "Locked",
    READY: "Ready",
    RUNNING: "Running",
    COMPLETE: "Complete",
    STALE: "Out of date",
    FAILED: "Failed",
    STOPPED: "Stopped",
}


@dataclass(frozen=True)
class Stage:
    """One step of the pipeline."""

    key: str
    title: str
    blurb: str
    optional: bool = False

    requires: Tuple[str, ...] = ()
    """What must have produced something before this stage can run.

    Empty means every required stage before it, which is the ordinary case. Stated explicitly only
    for the stages that break the chain: coverage needs the scenario space and nothing after it,
    and gating it on the review would block transcripts behind the last model pass for no reason.
    """


# The order here is the order of the pipeline, and the chain is linear except where a stage names
# its own dependencies. Optional stages can be skipped without blocking what follows.
#
# One line each. A stage page has the run button, the inputs and the result on it; a paragraph
# explaining the stage above all three pushes the work below the fold and is read once.
STAGES: Tuple[Stage, ...] = (
    Stage("intake", "Intake",
          "The agent as a decision graph, drafted from the submitted documentation."),

    Stage("workflow", "Workflow",
          "Every route through each capability, walked exhaustively, plus the probes that apply."),

    Stage("scenarios", "Scenario space",
          "Each route written up for a tester: a name, what happens, and the turns to take."),

    Stage("variations", "Variation space",
          "Variants of each scenario worth running separately. Not built yet.",
          optional=True),

    Stage("materiality", "Materiality",
          "What a mishandled scenario would cost, and how many runs that justifies."),

    Stage("review", "Review",
          "One pass over the whole space against the documentation: settles materiality, flags "
          "weak scenarios, proposes what enumeration could not reach."),

    Stage("coverage", "Coverage",
          "How much of the space the model owner's own testing already reaches.",
          optional=True, requires=("intake", "workflow")),

    Stage("summary", "Summary",
          "The data template to issue, and what still has to be asked for."),
)

STAGE_BY_KEY: Dict[str, Stage] = {stage.key: stage for stage in STAGES}
STAGE_KEYS: Tuple[str, ...] = tuple(stage.key for stage in STAGES)

# What a stage used to be called, so a workspace recorded under the old names still opens.
# Reading the documents and drafting the intake used to be two stages and are now one, which is
# why "documents" maps onto "intake": whatever the old documents stage produced is now part of
# what the intake stage produces, and the intake stage is where it is shown.
RENAMED: Dict[str, str] = {
    "documents": "intake",
    "benchmark": "workflow",
    "text": "scenarios",
    "issue": "summary",
}


def current_key(key: str) -> str:
    """The key a stage goes by now, given whatever it was called when it was recorded."""
    return RENAMED.get(key, key)


def index_of(key: str) -> int:
    """Position of a stage in the pipeline. Raises for an unknown key rather than guessing."""
    try:
        return STAGE_KEYS.index(key)
    except ValueError:
        raise KeyError(f"unknown stage: {key}") from None


def required_before(key: str) -> List[Stage]:
    """The stages that must have produced something before this one can run.

    A stage naming its own dependencies is taken at its word; otherwise it is every required stage
    ahead of it in the pipeline. Optional stages are never a prerequisite either way, which is what
    lets a team that already has a completed intake workbook start at the intake stage and never
    open the document stages at all.
    """
    stage = STAGE_BY_KEY[key]
    if stage.requires:
        return [STAGE_BY_KEY[k] for k in stage.requires if not STAGE_BY_KEY[k].optional]
    return [earlier for earlier in STAGES[:index_of(key)] if not earlier.optional]


def downstream_of(key: str) -> List[Stage]:
    """Every stage after this one, in order. What a change here puts out of date."""
    return list(STAGES[index_of(key) + 1:])
