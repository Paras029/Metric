"""What the declared intake still needs, as questions scoped to the row that needs them.

A question is only worth asking if the person reading it can answer it. Questions scoped to the
*evidence* -- broad facets like "capabilities" or "risk areas" -- say which document section is
thin, never which capability, decision or state the intake needs filled in, and "what does the
agent do about risk?" has no single right length of answer and no obvious place to put one. A
person can answer "what should DEC-05's second outcome be called, and what decides between the
two?" in a sentence, because the question already says exactly what row of the intake it is going
to fill in.

So this module reads the *intake itself* -- whatever is currently declared, drafted or hand-edited
-- for the places it is structurally thin, and returns one question per gap, addressed to the
specific capability, decision, state, tool or persona it concerns. Nothing here calls a model:
every check is a property a well-formed intake needs in order to be walked at all, or a field the
rest of the pipeline reads and a blank value silently degrades (a capability with no type drops
the probes that would have tested it; a terminal state with no outcome type cannot be categorised).
A softer, better-informed reading of what is *weak* rather than strictly missing comes from the
drafter's own review notes -- see :func:`core.intake.read_review_notes` -- which this does not
duplicate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set

from metric.shared.text import name_key
from metric.domain.graph import DecisionGraph
from metric.domain.models import IntakeData

# Kinds of row a gap can be addressed to. "" is cross-cutting -- about the use case as a whole,
# or about something no single row owns.
USE_CASE, PERSONA, CAPABILITY, DECISION, STATE, TOOL = (
    "use_case", "persona", "capability", "decision", "state", "tool")


@dataclass
class Gap:
    """One thing the intake needs, addressed to the row it concerns."""

    kind: str
    target_id: str
    field: str
    question: str
    why: str

    example: str = ""
    """What an answer to this looks like, shown in the box the answer is typed into.

    A question can be perfectly clear about what it is asking and still leave someone unsure what
    shape the reply should take -- whether "what are all the ways this can resolve" wants a
    sentence, a list, or two words. One concrete example settles that in the place it is asked,
    which is faster than any amount of explanation elsewhere.
    """

    @property
    def heading(self) -> str:
        return f"{self.target_id}" if self.target_id else "Use case"


def _reachable_states(graph: DecisionGraph) -> Set[str]:
    """Every state a walk from the start can actually arrive at."""
    reached = set(graph.start_states)
    frontier = list(graph.start_states)
    while frontier:
        state = graph.state(frontier.pop())
        if state is None:
            continue
        for decision_id in state.next_decisions:
            decision = graph.decision(decision_id)
            if decision is None:
                continue
            for variant in decision.variants:
                target = graph.successor(decision_id, variant)
                if target not in reached and not target.startswith("OUT:"):
                    reached.add(target)
                    frontier.append(target)
    return reached


def _use_case_gaps(intake: IntakeData) -> List[Gap]:
    gaps = []
    fields = [
        ("Use case name", "name", "What is this agent called?",
         "Disputes Assistant"),
        ("Business objective", "objective",
         "What does this agent exist to do, and what does success look like for the business?",
         "Resolve card disputes end to end without a person, for the routine cases."),
        ("Agent type", "agent_type", "What kind of agent is this -- a chatbot, a voice agent, a "
                                     "planner acting without a live conversation?",
         "Chatbot in the servicing app"),
        ("Success criteria", "success_criteria",
         "What does a successful interaction with this agent look like?",
         "The dispute is filed and the user is told the reference number and the timescale."),
    ]
    for label, key, question, example in fields:
        if not str(intake.use_case.get(label, "")).strip():
            gaps.append(Gap(USE_CASE, "", key, question,
                            "Every scenario is written against this; a blank here weakens the "
                            "framing of all of them, not just one.", example=example))
    return gaps


def _persona_gaps(intake: IntakeData) -> List[Gap]:
    gaps = []
    for persona in intake.personas:
        applies_to = " ".join(persona.applies_to) if isinstance(persona.applies_to, list) \
            else str(persona.applies_to)
        if len(applies_to.strip()) < 8:
            gaps.append(Gap(
                PERSONA, persona.id, "applies_to",
                f"What is {persona.name or persona.id} trying to achieve, specifically -- what "
                f"does the agent do differently for them?",
                "A persona with no stated objective cannot be told apart from a mood or a "
                "writing style, and cannot inform how a scenario for them should read.",
                example="Wants the charge reversed today and has already called twice."))
    return gaps


def _capability_gaps(intake: IntakeData) -> List[Gap]:
    gaps = []
    used = {d.trigger_capability for d in intake.decisions if d.trigger_capability}
    for capability in intake.capabilities:
        if not capability.type:
            gaps.append(Gap(
                CAPABILITY, capability.id, "type",
                f"What kind of capability is {capability.name or capability.id} -- Lookup, "
                f"Transactional, Gating, Advisory, or PII-handling?",
                "The type decides which adversarial probes apply. Left blank, this capability "
                "is silently tested less than the others.",
                example="Gating"))
        if capability.id not in used:
            gaps.append(Gap(
                CAPABILITY, capability.id, "decisions",
                f"Which decision in the agent actually exercises {capability.name or capability.id}?",
                "A capability nothing branches on contributes no scenarios -- either it needs a "
                "decision, or it does not belong in this intake as its own capability.",
                example="DEC-04"))
    # Asked only where *some* capability has a span. An intake nobody has divided yet walks the
    # whole graph and is complete as it stands, so asking this of every capability on a first run
    # would put a question against every row of a sheet nobody has got to -- which is how a list
    # of real gaps stops being read.
    # Documentation written at capability level and read as though it were at decision level.
    # A capability with exactly one decision, named the same thing, is almost always "the agent
    # identifies the customer" turned into a decision because a decision was what the form asked
    # for -- and its outcomes are then whatever the reading supposed they were rather than what
    # the agent does. That is worse than a thin declaration: a thin one can be asked about, and
    # scenarios built on supposed outcomes cannot be told from real ones by anybody downstream.
    by_capability: Dict[str, List] = {}
    for decision in intake.decisions:
        by_capability.setdefault(decision.trigger_capability, []).append(decision)
    for capability in intake.capabilities:
        inside = by_capability.get(capability.id, [])
        if len(inside) == 1 and name_key(inside[0].name) == name_key(capability.name):
            gaps.append(Gap(
                CAPABILITY, capability.id, "granularity",
                f"What does the agent actually decide inside {capability.name or capability.id}, "
                f"and how can each of those decisions turn out?",
                f"{capability.id} holds a single decision of the same name, which usually means "
                f"the documentation described this capability as one step and it was recorded as "
                f"one decision. A capability is a group of decisions; if there is genuinely only "
                f"one, say so and this stops being asked.",
                example="Matches the record / does not match / no record found"))

    partly_drawn = any(c.is_bounded for c in intake.capabilities)
    for capability in intake.capabilities:
        if partly_drawn and not capability.is_bounded:
            named = capability.name or capability.id
            gaps.append(Gap(
                CAPABILITY, capability.id, "span",
                f"Which state does {named} start from, and which states does it hand on or "
                f"finish at?",
                "Scenarios are enumerated one capability at a time, so a capability without both "
                "an entry and an exit is walked by nothing and contributes no scenarios at all. "
                "The other capabilities here have a span, so this one is a gap rather than a "
                "choice not to divide the graph.",
                example="entered at S-04; hands on or ends at S-07, S-08"))
    return gaps


def _decision_gaps(intake: IntakeData, graph: DecisionGraph) -> List[Gap]:
    gaps = []
    for decision in intake.decisions:
        if decision.out_of_scope:
            continue
        if len(decision.variants) < 2:
            gaps.append(Gap(
                DECISION, decision.id, "outcomes",
                f"{decision.id} ({decision.name}) names "
                f"{'no outcome' if not decision.variants else 'only one outcome'} -- what are "
                f"all the ways this can resolve, and what decides between them?",
                "A branch point with fewer than two named outcomes adds no routes, so nothing "
                "here is ever tested.",
                example="Verified / Not verified — whether the postcode given matches the file"))
        for variant in decision.variants:
            target = graph.successor(decision.id, variant)
            if target.startswith("OUT:"):
                gaps.append(Gap(
                    DECISION, decision.id, "outcomes",
                    f"Where does {decision.id}'s \"{variant}\" outcome lead? No state in the "
                    f"intake declares itself reached by it.",
                    "An outcome with no destination stops the route there whether or not that "
                    "was intended.",
                    example="S-07, the state where the user is asked to try again"))
        if not graph.states_offering(decision.id):
            gaps.append(Gap(
                DECISION, decision.id, "reached_from",
                f"How is {decision.id} ({decision.name}) arrived at? No state in the intake "
                f"lists it under Valid Next Decisions, so no walk of the graph ever runs it.",
                "A decision nothing routes into is not part of the graph at all -- it is drawn "
                "as a fragment below the flow and contributes no scenarios, however completely "
                "its outcomes are described.",
                example="S-04, the state where identity has just been confirmed"))
        if not decision.trigger_capability:
            gaps.append(Gap(
                DECISION, decision.id, "capability",
                f"Which capability does {decision.id} ({decision.name}) belong to?",
                "Without this, tools linked to that capability are not associated with the "
                "decision that uses them.",
                example="CAP-02"))
    return gaps


def _state_gaps(intake: IntakeData, graph: DecisionGraph, reachable: Set[str]) -> List[Gap]:
    gaps = []
    for state in intake.states:
        if not state.description.strip():
            gaps.append(Gap(
                STATE, state.id, "description",
                f"What position is {state.id} -- what has happened, and what has the agent just "
                f"told or asked the user?",
                "A state with no description is unreadable in the graph and in any scenario "
                "that passes through it.",
                example="Identity confirmed; the agent has asked which charge is disputed."))
        if state.is_terminal and not state.outcome_type:
            gaps.append(Gap(
                STATE, state.id, "outcome_type",
                f"{state.id} ends the interaction -- is that a Happy path, Retry, Fallback, "
                f"Escalation or Termination?",
                "This is what sets the category of every scenario ending here. Left blank, "
                "those scenarios cannot be categorised at all.",
                example="Escalation"))
        if not state.is_terminal and not state.next_decisions:
            gaps.append(Gap(
                STATE, state.id, "next_decisions",
                f"What can happen after {state.id} ({state.description or 'no description'})? "
                f"It is not marked as ending the interaction, but nothing follows it either.",
                "Either something follows this state, or it is terminal and needs an outcome "
                "type -- as declared, nothing is tested past this point.",
                example="DEC-06 runs next — or: nothing, this ends the interaction"))
        if state.id not in reachable and state.reached_via.strip().lower() != "start":
            gaps.append(Gap(
                STATE, state.id, "reached_via",
                f"What actually leads to {state.id}? As declared "
                f"({state.reached_via or 'nothing stated'}), no walk from the start ever "
                f"reaches it.",
                "A state nothing reaches is never the ending of any scenario, whatever it "
                "describes.",
                example="DEC-03 = Not verified"))
    return gaps


def _tool_gaps(intake: IntakeData) -> List[Gap]:
    gaps = []
    for tool in intake.tools:
        if not tool.capability_id:
            gaps.append(Gap(
                TOOL, tool.name, "capability_id",
                f"Which capability does calling {tool.name} belong to?",
                "Unlinked, this tool is not associated with the decision that calls it.",
                example="CAP-01"))
    return gaps


def find_gaps(intake: IntakeData) -> List[Gap]:
    """Every gap the current declaration has, row by row, most structurally important first.

    Order is deliberate: a decision that names no outcomes blocks enumeration entirely, so it is
    worth seeing before a persona's phrasing. Nothing here is a call a person cannot make in a
    sentence -- see the module docstring for why that bar matters.
    """
    graph = DecisionGraph(intake.decisions, intake.states)
    reachable = _reachable_states(graph)

    gaps: List[Gap] = []
    gaps += _use_case_gaps(intake)
    gaps += _decision_gaps(intake, graph)
    gaps += _state_gaps(intake, graph, reachable)
    gaps += _capability_gaps(intake)
    gaps += _tool_gaps(intake)
    gaps += _persona_gaps(intake)

    if not any(s.reached_via.strip().lower() == "start" for s in intake.states):
        gaps.insert(0, Gap(
            "", "", "start",
            "Which state is where the interaction opens?",
            "One state needs 'Start' in its Reached Via column, or the graph has no entry "
            "point and nothing can be walked."))
    return gaps
