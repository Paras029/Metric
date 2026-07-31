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

# Shown beside the stage number rather than as a block of prose. The label is the whole point;
# a reader who wants the reasoning has the stage's own write-up below it.
MODE_LABELS = {
    COMPUTED: "Deterministic",
    JUDGED: "LLM",
    REVIEW: "Human",
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
    mode: str
    blurb: str
    detail: str = ""
    optional: bool = False

    requires: Tuple[str, ...] = ()
    """What must have produced something before this stage can run.

    Empty means every required stage before it, which is the ordinary case: the pipeline is mostly
    a chain and each step consumes the last one's output. Stating it explicitly is for the stages
    that are not — measuring what the modelling team's own testing covers needs the benchmark and
    nothing after it, and gating it on the review as well would mean their file could not be
    submitted until the last model pass had finished, for no reason.
    """

    @property
    def mode_label(self) -> str:
        return MODE_LABELS[self.mode]


# The order here is the order of the pipeline, and the dependency chain is linear: each stage
# depends on the one before it. Optional stages can be skipped without blocking what follows.
STAGES: Tuple[Stage, ...] = (
    Stage("documents", "Documents", JUDGED,
          "Add whatever the model owner sent, then read it. Model documentation, vendor material, "
          "workflow diagrams and decks.",
          "Takes the submitted pack and reads it. Every passage is read for what it establishes, "
          "then each question is answered from everything found across all documents at once — "
          "documentation rarely answers them in one place. Statements are kept only where the "
          "quote supporting them is found in the source, and what the documents leave unsettled "
          "is recorded rather than filled in.",
          optional=True),

    Stage("intake", "Intake", REVIEW,
          "The agent described as a decision graph. Drafted from the evidence, corrected by you. "
          "Nothing reaches the benchmark that is not here.",
          "The authoritative description of the agent, and the boundary of what can be tested. It "
          "is drafted from the documents so the work is correction rather than transcription, and "
          "what it could not settle comes back as questions addressed to the specific decision, "
          "state, capability, tool or persona that needs them — answer what you can, then revise "
          "the declaration with those answers folded in. Upload a completed intake instead if you "
          "already have one."),

    Stage("benchmark", "Benchmark", COMPUTED,
          "Every distinct route through the decision graph, plus the adversarial probes that "
          "apply to this agent. Enumerated, not chosen.",
          "Walks the declared graph and turns every distinct route into a scenario, so coverage of "
          "the declared design is demonstrable rather than asserted. Adversarial probes are "
          "added separately, since they test properties of the agent rather than routes through "
          "it. No model decides which scenarios exist."),

    Stage("text", "Scenario text", JUDGED,
          "The description and tester script for each scenario, written in business language. "
          "This is what the model owner reads.",
          "Writes each scenario as instructions a tester can follow without knowing how the agent "
          "was built. The expected outcome is withheld from this step: what it produces is "
          "issued to the modelling team, and text that revealed the answer would leave the "
          "exercise measuring nothing."),

    Stage("materiality", "Materiality", JUDGED,
          "What it would cost the business if the agent handled each scenario badly, and how "
          "many runs that justifies.",
          "Assigns each scenario a tier by business consequence rather than abstract severity, "
          "judged across the set — whether a scenario matters depends partly on what else the "
          "benchmark covers. The tier decides how many runs each scenario is issued with, so it "
          "governs the size of the request you make."),

    Stage("review", "Final review", JUDGED,
          "One pass over the whole benchmark: settles materiality with everything in view, "
          "flags weak scenarios, and proposes what enumeration could not reach.",
          "The only step that sees the benchmark whole. Enumeration is exhaustive over what was "
          "declared but bounded by it, so this works at that boundary: it settles materiality, "
          "flags scenarios that are redundant, under-specified or mis-scoped, and proposes "
          "additions. It cannot remove anything — a flag is a recommendation to you."),

    Stage("coverage", "Coverage", JUDGED,
          "What the model owner's own testing already covers, matched against this benchmark. "
          "Skip it if they submitted none.",
          "Matches the modelling team's own scenarios against the benchmark, in whatever format "
          "they sent them. Reading this before the pack is issued is what lets it concentrate "
          "on what they have not covered. Route and persona must both match, because the same "
          "route walked by a different user is a different test.",
          optional=True, requires=("intake", "benchmark")),

    Stage("issue", "Issue", COMPUTED,
          "The challenge pack to send the model owner, and the registry you keep. The pack "
          "carries no expected outcomes.",
          "Writes the two workbooks. The challenge pack goes to the modelling team and carries no "
          "expected outcome, decision path or materiality. The registry stays with you and holds "
          "the ground truth. The pack is derived from the registry, so rebuild it after anything "
          "that changes the registry."),
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
    """The stage immediately before this one, or None for the first."""
    position = index_of(key)
    return STAGES[position - 1] if position else None


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
