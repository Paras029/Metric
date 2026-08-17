"""Shared dataclasses and closed vocabularies used across the generator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CATEGORIES = ["Happy path", "Retry", "Fallback", "Escalation", "Termination"]
# Three tiers, not four. A four-point scale invites the top two to be used
# interchangeably -- "very bad" and "extremely bad" are not a distinction anybody applies
# consistently across three hundred scenarios, and the run count that hangs off the tier made the
# inconsistency expensive. What the scale has to separate is what the agent is *for* from what
# supports it, and that is three buckets: the act, the approach to the act, and the rest.
MATERIALITY = ["Low", "Medium", "High"]
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

# Spellings an older metadata workbook may carry for an origin above. A metadata workbook is a file on disk that
# outlives any one run, so a value read out of one is translated to its settled name on the way in
# rather than left to fall outside FUNCTIONAL_ORIGINS and quietly drop those scenarios out of the
# data template.
LEGACY_ORIGINS = {"coverage-gap": ORIGIN_VARIANT_GAP}


def canonical_origin(raw: str) -> str:
    """One origin's settled name, translating what an older metadata workbook called it."""
    origin = str(raw or "").strip()
    return LEGACY_ORIGINS.get(origin, origin)

# Where a decision's input arrives from. Only "User" steps become conversational turns; the rest
# are internal, which is what makes planner and non-conversational agents representable.
INPUT_SOURCES = ["User", "Tool", "Memory-Session", "Memory-CrossSession",
                 "System-Context", "Document"]

# Runs the model owner is asked to execute per scenario. PLACEHOLDER — pending MRMG sign-off.
VARIATIONS_BY_MATERIALITY = {"Low": 1, "Medium": 3, "High": 8}


# --------------------------------------------------------------------------- intake
@dataclass(frozen=True)
class Capability:
    """One block of the agent's work, and the part of the graph that does it.

    A capability used to be a label on a decision and nothing else. It is now the unit the
    scenario space is enumerated over, because a whole-journey walk does not survive a real agent:
    an agent that identifies a cardmember, then verifies them, then verifies a charge has three
    blocks of roughly a dozen routes each, and walking it end to end multiplies them into hundreds
    of scenarios that differ only in how an earlier block was entered. Nobody tests those
    separately, and a pack of seven hundred is not a pack anybody runs.

    ``entry_states`` are the positions this block can be entered in; ``exit_states`` the positions
    it hands on or finishes in. Both are drawn by hand -- where one capability ends and the next
    begins is a judgement about the agent, not something a graph or a model can read off it -- and
    an exit of one block is ordinarily the entry of the next, which is what makes the two join up.
    """

    id: str
    name: str
    type: str = ""
    entry_states: Tuple[str, ...] = ()
    exit_states: Tuple[str, ...] = ()

    @property
    def is_bounded(self) -> bool:
        """Whether this capability has been given a span to walk."""
        return bool(self.entry_states and self.exit_states)


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
    capability_id: str = ""
    """The block this scenario walks. Empty only where the graph was walked whole."""
    precondition: str = ""
    """What has to be true before the tester starts, in words a tester can act on.

    A capability-scoped scenario does not begin at the start of the conversation: one that tests
    verification begins with a cardmember already identified. That is not a detail of the route --
    it is the first instruction the tester needs, and without it the conversation they run is a
    different one from the scenario being asked for.
    """
    turn_meta: List[TurnMeta] = field(default_factory=list)
    origin: str = "graph"
    name: str = ""                # issued to the owner -- a handle, not a summary
    description: str = ""          # issued to the owner
    turn_plan: str = ""           # issued to the owner
    materiality: str = "Medium"           # first LLM sweep
    materiality_confidence: str = "Low"   # first LLM sweep
    materiality_rationale: str = ""       # first LLM sweep
    materiality_override: str = ""        # human ruling; wins over everything

    # Review layer — a whole-space sweep run last. Recorded separately rather than
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
    """A generated scenario loaded back from the scenario space metadata — the scenario space coverage maps against.

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
    capability_id: str = ""
    """The block this scenario walks, empty where the graph was walked whole. Coverage reads it
    to know that a conversation crossing several blocks is credited to only one of them."""


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
