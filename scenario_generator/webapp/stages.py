"""The stages a use case moves through, and what each one costs the reader in trust.

Every stage carries a ``mode``, and it is the most important thing on the screen after the
stage's own name. The pipeline genuinely mixes three kinds of work, and a validator reading an
output needs to know which kind produced it:

    computed  deterministic. The same input gives the same output every time, and the result can
              be traced back to the rule that produced it. Nothing here needs second-guessing.
    judged    a model weighed something. Reproducible only in the loose sense; the output is an
              opinion with reasons attached, and it is the reader's job to disagree where they
              disagree.
    review    the person using the tool decides. Nothing advances until they say so.

Presenting these identically would be the single most misleading thing this interface could do.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

COMPUTED = "computed"
JUDGED = "judged"
REVIEW = "review"

MODE_LABELS = {
    COMPUTED: "Computed",
    JUDGED: "Model judgement",
    REVIEW: "Your decision",
}

MODE_NOTES = {
    COMPUTED: "Deterministic. The same inputs always produce this same output.",
    JUDGED: "A model weighed this. Read the reasoning and overrule it where you disagree.",
    REVIEW: "Nothing moves forward until you approve it.",
}

# Stage status. A stage that has run and then had its inputs changed underneath it is stale
# rather than complete: its output still exists and can still be read, but it no longer reflects
# what it was built from, and saying so is the whole reason this state exists.
LOCKED = "locked"
READY = "ready"
RUNNING = "running"
COMPLETE = "complete"
STALE = "stale"
FAILED = "failed"

STATUS_LABELS = {
    LOCKED: "Locked",
    READY: "Ready",
    RUNNING: "Running",
    COMPLETE: "Complete",
    STALE: "Out of date",
    FAILED: "Failed",
}


@dataclass(frozen=True)
class Stage:
    """One step of the pipeline, as the person using it experiences it."""

    key: str
    title: str
    mode: str
    blurb: str
    optional: bool = False

    @property
    def mode_label(self) -> str:
        return MODE_LABELS[self.mode]

    @property
    def mode_note(self) -> str:
        return MODE_NOTES[self.mode]


# The order here is the order of the pipeline, and the dependency chain is linear: each stage
# depends on the one before it. Optional stages can be skipped without blocking what follows.
STAGES: Tuple[Stage, ...] = (
    Stage("sources", "Submitted documents", REVIEW,
          "Add the model documentation, vendor material, workflow diagrams and decks the model "
          "owner sent. Trim anything that has no bearing on how the agent behaves."),
    Stage("evidence", "Extracted evidence", JUDGED,
          "What the documents were found to say, each statement tied to the page it came from. "
          "Anything that could not be traced to a source was dropped."),
    Stage("questions", "Open questions", REVIEW,
          "What the documents did not cover. Your answers are recorded as evidence in their own "
          "right, attributed to you rather than to a document."),
    Stage("intake", "Intake", REVIEW,
          "The agent described as a decision graph. Drafted from the evidence, corrected by you. "
          "Nothing reaches the benchmark that is not here."),
    Stage("benchmark", "Benchmark", COMPUTED,
          "Every distinct route through the decision graph, plus the adversarial probes that "
          "apply to this agent. Enumerated, not chosen."),
    Stage("text", "Scenario text", JUDGED,
          "The description and tester script for each scenario, written in business language. "
          "This is what the model owner reads."),
    Stage("materiality", "Materiality", JUDGED,
          "What it would cost the business if the agent handled each scenario badly, and how "
          "many runs that justifies."),
    Stage("review", "Final review", JUDGED,
          "One pass over the whole benchmark: settles materiality with everything in view, "
          "flags weak scenarios, and proposes what enumeration could not reach."),
    Stage("issue", "Issue", COMPUTED,
          "The challenge pack to send the model owner, and the registry you keep. The pack "
          "carries no expected outcomes."),
    Stage("coverage", "Coverage", JUDGED,
          "How much of this benchmark the model owner's own testing already covers.",
          optional=True),
)

STAGE_BY_KEY: Dict[str, Stage] = {stage.key: stage for stage in STAGES}
STAGE_KEYS: Tuple[str, ...] = tuple(stage.key for stage in STAGES)


def index_of(key: str) -> int:
    """Position of a stage in the pipeline. Raises for an unknown key rather than guessing."""
    try:
        return STAGE_KEYS.index(key)
    except ValueError:
        raise KeyError(f"unknown stage: {key}") from None


def predecessor(key: str) -> Optional[Stage]:
    """The stage that must be complete before this one can run, or None for the first."""
    position = index_of(key)
    return STAGES[position - 1] if position else None


def downstream_of(key: str) -> List[Stage]:
    """Every stage after this one, in order. What a change here puts out of date."""
    return list(STAGES[index_of(key) + 1:])
