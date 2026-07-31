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

# Origins that represent the functional benchmark. Coverage matching runs over these only —
# probes are excluded by origin, not by having an empty decision path.
FUNCTIONAL_ORIGINS = ("graph", "coverage-gap")

# Where a decision's input arrives from. Only "User" steps become conversational turns; the rest
# are internal, which is what makes planner and non-conversational agents representable.
INPUT_SOURCES = ["User", "Tool", "Memory-Session", "Memory-CrossSession",
                 "System-Context", "Document"]

# Runs the modeling team is asked to execute per scenario. PLACEHOLDER — pending MRMG sign-off.
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

    # What the modelling team's own testing already covers, written by the coverage stage. An
    # annotation and nothing more: it is recorded against the scenario and shown to a person, and
    # never removes anything from the pack. Whether covering a scenario twice is waste or
    # confirmation depends on how much their testing is trusted, and that is not a judgement this
    # tool is in a position to make.
    owner_coverage: str = ""              # "Covered", "Partially covered" or ""
    owner_coverage_note: str = ""

    # Probe provenance. Empty for graph scenarios; MRMG-internal, never issued to the owner.
    probe_id: str = ""
    probe_family: str = ""

    # Proposal provenance. Set only on scenarios the review layer added.
    proposed_rationale: str = ""
    proposed_anchor: str = ""

    @property
    def is_probe(self) -> bool:
        return self.origin == "probe"

    @property
    def is_proposed(self) -> bool:
        return self.origin == "llm-proposed"

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
class BenchmarkScenario:
    """A generated scenario loaded back from the registry — the benchmark coverage maps against.

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
    """One row of a modelling team's own scenario library, where they keep one.

    Not what the coverage stage reads -- that works from transcripts, since a scenario list is a
    claim about their testing rather than the testing itself. This is context handed to the final
    review, so a proposal can take account of what they say they already cover.
    """
    id: str
    description: str
    declared_path: str = ""


