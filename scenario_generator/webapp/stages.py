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
    detail: str = ""
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
          "owner sent. Trim anything that has no bearing on how the agent behaves.",
          "The benchmark can only test what is known about the agent, so this pack determines "
          "the ceiling on what the validation can reach. Supporting material is read for what it "
          "establishes about the agent's behaviour; approval histories, infrastructure detail "
          "and change logs are passed over. A document that arrives without a text layer, such "
          "as a scanned PDF, is refused with a reason rather than accepted and quietly ignored."),

    Stage("evidence", "Extracted evidence", JUDGED,
          "What the documents were found to say, each statement tied to the page it came from. "
          "Anything that could not be traced to a source was dropped.",
          "Each passage is read for the eleven categories the benchmark depends on, and every "
          "statement returned is paired with the words it was drawn from. Those words are then "
          "checked against the document itself. A statement whose quote cannot be located is "
          "discarded rather than flagged, on the basis that an unfounded citation is more "
          "damaging than no citation at all. The count of discards is reported, because a high "
          "one is evidence about the pack or the method rather than a detail to absorb quietly."),

    Stage("questions", "Open questions", REVIEW,
          "What the documents did not cover. Your answers are recorded as evidence in their own "
          "right, attributed to you rather than to a document.",
          "Two kinds of question arrive here. Where no submitted document addressed a category "
          "at all, the gap is raised against the pack. Where a statement could not be checked "
          "against text, because it was read from a diagram or supplied by a person, it is "
          "raised for confirmation. Both are treated as unknown rather than as absent from the "
          "agent, since a silent gap and a declared exclusion have very different consequences "
          "for a benchmark."),

    Stage("intake", "Intake", REVIEW,
          "The agent described as a decision graph. Drafted from the evidence, corrected by you. "
          "Nothing reaches the benchmark that is not here.",
          "The intake is the authoritative description of the agent and the boundary of what can "
          "be tested. It is drafted from the evidence so that the work is one of correction "
          "rather than transcription, but it is not authoritative until it has been reviewed. "
          "The declared graph is shown alongside it so that a missing branch or an unreachable "
          "outcome is visible as a shape rather than buried in a spreadsheet row."),

    Stage("benchmark", "Benchmark", COMPUTED,
          "Every distinct route through the decision graph, plus the adversarial probes that "
          "apply to this agent. Enumerated, not chosen.",
          "Routes are enumerated exhaustively by walking the declared graph, so coverage of the "
          "declared design is demonstrable rather than asserted, and every scenario traces back "
          "to the path that produced it. Adversarial and non-functional probes are applied "
          "separately, since they test properties of the agent rather than routes through it, "
          "and which of them apply is determined mechanically from what the intake declares. No "
          "model decides which scenarios exist."),

    Stage("text", "Scenario text", JUDGED,
          "The description and tester script for each scenario, written in business language. "
          "This is what the model owner reads.",
          "Each scenario is written up as instructions a competent tester can follow without "
          "knowing how the agent was built. The expected outcome is deliberately withheld from "
          "this step: what is produced here is issued to the team that owns the agent, and text "
          "that revealed the expected result would leave the exercise measuring nothing."),

    Stage("materiality", "Materiality", JUDGED,
          "What it would cost the business if the agent handled each scenario badly, and how "
          "many runs that justifies.",
          "Materiality is assessed as business consequence rather than abstract severity, and "
          "across the set rather than scenario by scenario, because whether a scenario matters "
          "depends partly on what else the benchmark already covers. Redundancy and relative "
          "depth are computed before any assessment and supplied as evidence. The resulting tier "
          "determines how many runs each scenario is issued with, so it governs the size of the "
          "request made of the model owner."),

    Stage("review", "Final review", JUDGED,
          "One pass over the whole benchmark: settles materiality with everything in view, "
          "flags weak scenarios, and proposes what enumeration could not reach.",
          "This is the only step that sees the benchmark whole. Enumeration is exhaustive over "
          "what was declared but bounded by it, and the probe library cannot know what this "
          "particular business makes risky; the review exists to work at that boundary. It may "
          "settle materiality, flag a scenario as redundant, under-specified or mis-scoped, and "
          "propose additions validated against the intake's own vocabulary. It cannot remove "
          "anything: a flag is a recommendation to a person."),

    Stage("issue", "Issue", COMPUTED,
          "The challenge pack to send the model owner, and the registry you keep. The pack "
          "carries no expected outcomes.",
          "Two workbooks are produced and the separation between them is the point of the "
          "exercise. The challenge pack is issued to the team that owns the agent and contains "
          "no expected outcome, no decision path and no materiality. The registry is retained "
          "and holds the full ground truth. The pack is derived from the registry rather than "
          "authored, so it must be rebuilt after anything that changes the registry."),

    Stage("coverage", "Coverage", JUDGED,
          "How much of this benchmark the model owner's own testing already covers.",
          "The owner's own scenario library is mapped onto the intake's vocabulary and matched "
          "against the benchmark. Route and persona together form a single strict gate, because "
          "the same route walked by a different kind of user is a different test. How well a "
          "scenario was understood is reported separately from whether it matched, so a weak "
          "match and a weak reading are not confused.",
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
