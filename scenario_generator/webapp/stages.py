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
    """One step of the pipeline, as the person using it experiences it."""

    key: str
    title: str
    blurb: str
    detail: str = ""
    optional: bool = False

    requires: Tuple[str, ...] = ()
    """What must have produced something before this stage can run.

    Empty means every required stage before it, which is the ordinary case: the pipeline is mostly
    a chain and each step consumes the last one's output. Stating it explicitly is for the stages
    that are not — measuring what the model owner's testing covers needs the scenario space and
    nothing after it, and gating it on the review as well would mean the transcripts could not be
    submitted until the last model pass had finished, for no reason.
    """


# The order here is the order of the pipeline, and the dependency chain is linear: each stage
# depends on the one before it. Optional stages can be skipped without blocking what follows.
#
# The blurb is the whole of what a stage says about itself before you run it. One sentence, in
# the language a business reader already has: it is read by a validator on their first day and by
# somebody's director on the way past their desk, and neither of them is served by a paragraph.
# The detail below it is for the person who wants the reasoning, folded away until asked for.
STAGES: Tuple[Stage, ...] = (
    Stage("intake", "Intake drafting",
          "What the agent is, as a decision graph. Drafted from the model owner's "
          "documentation, then confirmed by you.",
          "Reads everything submitted — documentation, vendor material, workflow diagrams — and "
          "drafts the declaration the whole scenario space is built on. Reading and drafting are one "
          "step because they are one job: the reading exists to be drafted from, and a person has "
          "no decision to make between them. Whatever the documents leave unsettled is folded into "
          "the draft as a note against the row it concerns, so the work is correction rather than "
          "transcription. Upload a completed intake instead if you already have one — it is then "
          "read and never written to.\n\n"
          "This is the boundary of what can be tested. Nothing absent from here reaches the "
          "scenario space."),

    Stage("workflow", "Workflow",
          "Every distinct route through the graph, walked exhaustively. Plus the adversarial "
          "probes that apply to this agent.",
          "Walks the declared graph depth-first and turns every distinct route into a scenario, so "
          "coverage of the declared design is demonstrable rather than asserted. Adversarial "
          "probes are added separately, since they test properties of the agent rather than routes "
          "through it. No model decides which scenarios exist — this stage is the reason the "
          "scenario space can be defended."),

    Stage("scenarios", "Scenario space",
          "Each route written up as something a tester can run: a name, what happens, and the "
          "turns to take.",
          "Writes each scenario in business language, for somebody who has never seen how the "
          "agent was built. The expected outcome is withheld from this step by construction — it "
          "is never put in the prompt — because what this produces is issued to the model owner, "
          "and text that revealed the answer would leave the exercise measuring nothing."),

    Stage("variations", "Variation space",
          "The variants of each scenario worth running separately — different phrasing, different "
          "user, different conditions.",
          "A scenario says what is being tested; a variation says how else the same test can "
          "arrive. Not built yet: the stage is here so the shape of the pipeline is the shape you "
          "will use, and it passes through without changing anything.",
          optional=True),

    Stage("materiality", "Materiality",
          "What it would cost the business if the agent handled each scenario badly, and how many "
          "runs that justifies.",
          "Assigns each scenario a tier by business consequence rather than abstract severity, "
          "judged across the set — whether a scenario matters depends partly on what else the "
          "scenario space covers. The tier decides how many runs each scenario is issued with, so it "
          "governs the size of the request you make."),

    Stage("review", "Review",
          "One pass over the whole space, with the documentation still in view. Settles "
          "materiality, flags what is weak, proposes what was missed.",
          "The only step that sees the scenario and variation space whole, against the context "
          "read at intake. Enumeration is exhaustive over what was declared but bounded by it, so "
          "this works at that boundary: it settles materiality with everything in view, checks the "
          "ending each scenario is filed under, flags what is redundant, under-specified or "
          "mis-scoped, and proposes what enumeration could not reach. It cannot remove anything — "
          "a flag is a recommendation to you."),

    Stage("coverage", "Coverage",
          "How much of the space the model owner's testing already reaches. Skip it if they "
          "submitted none.",
          "Reads the transcripts of the testing already done and maps each conversation onto at "
          "most one scenario, decided by where the exchange ends rather than by what it passes "
          "through — a conversation that authenticates and stops is not evidence for a scenario "
          "that authenticates and then does something else. What comes out is a count per "
          "scenario rather than a covered/not-covered flag, because one conversation and forty are "
          "not the same evidence.",
          optional=True, requires=("intake", "workflow")),

    Stage("summary", "Summary",
          "What to send the model owner: the challenge pack, and what still has to be asked for.",
          "Writes the two workbooks and states what is outstanding. The challenge pack goes to the "
          "model owner and carries no expected outcome, decision path or materiality. The registry "
          "stays with you and holds the ground truth. Alongside them: what the documentation never "
          "settled, what the review flagged, and where the model owner's evidence is thin — "
          "the request to make of them, in one place."),
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
