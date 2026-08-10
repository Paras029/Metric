"""Shared dataclasses and closed vocabularies used across the generator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CATEGORIES = ["Happy path", "Retry", "Fallback", "Escalation", "Termination"]
MATERIALITY = ["Low", "Medium", "High", "Critical"]
CONFIDENCE = ["Low", "Medium", "High"]

# What a capability does. Drives probe applicability, so it is a closed vocabulary rather than
# free text. Anything unrecognised is kept verbatim and simply matches no predicate.
CAPABILITY_TYPES = ["Lookup", "Transactional", "Gating", "Advisory", "PII-handling"]

# Where a scenario came from. One vocabulary, named here, because five modules produce and consume
# these and a bare string in each is how a value drifts into a workbook that nothing recognises.
ORIGIN_GRAPH = "graph"                 # a route the depth-first walk reached
ORIGIN_VARIANT_GAP = "variant-gap"     # an outcome the walk missed, given a route of its own
ORIGIN_PROBE = "probe"                 # path-independent, from the probe library
ORIGIN_PROPOSED = "llm-proposed"       # added by the review, and marked as such wherever it shows

ORIGINS = (ORIGIN_GRAPH, ORIGIN_VARIANT_GAP, ORIGIN_PROBE, ORIGIN_PROPOSED)

# Origins that represent the functional scenario space -- routes through the declared graph, however
# they were reached. Probes are excluded by origin rather than by having an empty decision path,
# which stops a proposal with no declared route from being mistaken for one.
FUNCTIONAL_ORIGINS = (ORIGIN_GRAPH, ORIGIN_VARIANT_GAP)

# Spellings an older registry may carry for an origin above. A registry is a file on disk that
# outlives any one run, so a value read out of one is translated to its settled name on the way in
# rather than left to fall outside FUNCTIONAL_ORIGINS and quietly drop those scenarios out of the
# challenge pack.
LEGACY_ORIGINS = {"coverage-gap": ORIGIN_VARIANT_GAP}


def canonical_origin(raw: str) -> str:
    """One origin's settled name, translating what an older registry called it."""
    origin = str(raw or "").strip()
    return LEGACY_ORIGINS.get(origin, origin)

# Where a decision's input arrives from. Only "User" steps become conversational turns; the rest
# are internal, which is what makes planner and non-conversational agents representable.
INPUT_SOURCES = ["User", "Tool", "Memory-Session", "Memory-CrossSession",
                 "System-Context", "Document"]

# Runs the model owner is asked to execute per scenario. PLACEHOLDER — pending MRMG sign-off.
RUNS_BY_MATERIALITY = {"Low": 1, "Medium": 3, "High": 5, "Critical": 10}


# --------------------------------------------------------------------------- intake
@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    type: str = ""


@dataclass(frozen=True)
class Decision:
    id: str
    name: str
    trigger_capability: str
    inputs: str
    variants: List[str]
    input_source: str = "User"
    max_attempts: int = 1
    outcome_condition: str = ""
    out_of_scope: bool = False
    """Declared, but not walked: a part of the agent this review is not testing.

    Set for a component that has already been reviewed elsewhere and is being reused as-is --
    the usual case is a plug-and-play sub-system covered by a separate engagement. The decision
    still belongs in the intake, because the graph is not honest without it, but nothing downstream
    builds a scenario through it: see :mod:`.graph`, which is the one place this is read."""


@dataclass(frozen=True)
class State:
    id: str
    reached_via: str
    description: str
    next_decisions: List[str]
    is_terminal: bool
    outcome_type: str = ""


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    applies_to: List[str] = field(default_factory=list)
    is_default: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    capability_id: str
    state_changing: bool = False


@dataclass
class IntakeData:
    use_case: dict
    personas: List[Persona]
    capabilities: List[Capability]
    decisions: List[Decision]
    states: List[State]
    tools: List[Tool]

    @property
    def name(self) -> str:
        return self.use_case.get("Use case name") or "(unnamed use case)"

    @property
    def objective(self) -> str:
        return self.use_case.get("Business objective") or "(not stated)"

    def persona_by_id(self, persona_id: str) -> Optional[Persona]:
        return next((p for p in self.personas if p.id == persona_id), None)


# --------------------------------------------------------------------------- scenarios
@dataclass(frozen=True)
class Step:
    """One edge of a walked path: a decision resolving to a variant, landing in a state."""
    decision_id: str
    variant: str
    next_state: str

    def __str__(self) -> str:
        return f"{self.decision_id}={self.variant}"


@dataclass(frozen=True)
class TurnMeta:
    """Per-step ground truth from the graph. MRMG-internal, never issued to the owner."""
    index: int
    decision_id: str
    decision_name: str
    expected_variant: str
    expected_tool: str
    next_state: str
    input_source: str = "User"


@dataclass
class Scenario:
    id: str
    path: List[Step]
    category: str
    persona: Persona
    seeded_state: str
    termination: str
    capabilities: List[str]
    tools: List[str]
    touches_state_change: bool
    turn_meta: List[TurnMeta] = field(default_factory=list)
    origin: str = "graph"
    name: str = ""                # issued to the owner -- a handle, not a summary
    description: str = ""          # issued to the owner
    turn_plan: str = ""           # issued to the owner
    materiality: str = "Medium"           # first LLM sweep
    materiality_confidence: str = "Low"   # first LLM sweep
    materiality_rationale: str = ""       # first LLM sweep
    materiality_override: str = ""        # human ruling; wins over everything

    # Review layer — a whole-registry sweep run last. Recorded separately rather than
    # overwriting, so the first assessment and the revision are both auditable.
    review_materiality: str = ""
    review_rationale: str = ""
    review_flag: str = ""                 # e.g. "Redundant", "Under-specified", "Mis-scoped"

    # The review's reading of the scenario's category. Category is otherwise deterministic --
    # taken from the Outcome Type the intake declares on the state a route ends in -- so a
    # disagreement here is usually a defect in that declaration rather than in the scenario, and
    # the fix belongs in the workbook. Recorded beside the declared value rather than replacing
    # it, for the same reason materiality is: both readings stay visible and a person rules.
    review_category: str = ""
    review_category_rationale: str = ""

    # How much of the model owner's testing landed on this scenario, written by the
    # coverage stage: a count of their conversations ("3 conversations"), and the ids behind it.
    # An annotation and nothing more -- it is recorded against the scenario and shown to a person,
    # and by itself removes nothing from the pack. Whether covering a scenario twice is waste or
    # confirmation depends on how far their testing is trusted, which is a judgement for the
    # person issuing the pack. It never reaches the pack itself: telling the team which scenarios
    # are already considered answered would tell them which ones to concentrate on.
    owner_coverage: str = ""
    owner_coverage_note: str = ""

    # Probe provenance. Empty for graph scenarios; MRMG-internal, never issued to the owner.
    probe_id: str = ""
    probe_family: str = ""

    # Proposal provenance. Set only on scenarios the review layer added.
    proposed_rationale: str = ""
    proposed_anchor: str = ""

    @property
    def is_probe(self) -> bool:
        return self.origin == ORIGIN_PROBE

    @property
    def is_proposed(self) -> bool:
        return self.origin == ORIGIN_PROPOSED

    @property
    def effective_materiality(self) -> str:
        """Human override, else the review layer's revision, else the first assessment."""
        return self.materiality_override or self.review_materiality or self.materiality

    @property
    def effective_category(self) -> str:
        """The review's reading where it disagreed, else what the intake declared."""
        return self.review_category or self.category

    @property
    def signature(self) -> Tuple[Tuple[str, str], ...]:
        return tuple((s.decision_id, s.variant) for s in self.path)

    @property
    def path_str(self) -> str:
        return " -> ".join(str(s) for s in self.path) if self.path else "-"

    @property
    def user_turns(self) -> List[TurnMeta]:
        """Steps the tester actually drives. Empty for a fully internal (batch/planner) path."""
        return [t for t in self.turn_meta if t.input_source == "User"]

    @property
    def turn_count(self) -> int:
        """Conversational turns to script. A path with no user step is a single trigger."""
        return len(self.user_turns) or 1


@dataclass
class ScenarioRow:
    """A generated scenario loaded back from the registry — the scenario space coverage maps against.

    Carries the category and materiality already assigned, so coverage never recomputes them.
    """
    id: str
    path_str: str
    category: str
    materiality: str
    capabilities: List[str]
    persona_id: str
    signature: Tuple[Tuple[str, str], ...]
    origin: str = "graph"


# --------------------------------------------------------------------------- their own material
@dataclass(frozen=True)
class OwnerScenario:
    """One row of a model owner's scenario library, where they keep one.

    Not what the coverage stage reads -- that works from transcripts, since a scenario list is a
    claim about their testing rather than the testing itself. This is context handed to the final
    review, so a proposal can take account of what they say they already cover.
    """
    id: str
    description: str
    declared_path: str = ""
