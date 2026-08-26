"""Check the walk against the declaration it came from.

The enumeration is the one part of this tool nothing downstream can audit. A scenario space of
three hundred routes over a forty-decision graph is not something anybody reads for correctness,
and both ways it can be wrong are silent: a route that could not actually happen looks like a
scenario, and a route that is missing looks like nothing at all.

So this module asks four questions, and every one of them is answered **without calling the
walk**. Re-using :func:`core.graph.walk_paths` to check :func:`core.graph.walk_paths` would only
prove it agrees with itself.

    Replayable   Can each recorded route actually be taken, step by step, from where the
                 scenario says it starts? A route that cannot be replayed is invented.
    Complete     Which declared outcomes, states and capabilities does no route touch? An
                 element with no route is untested, and the count is the honest answer to
                 "did we get everything".
    Counted      How many routes *should* there be? Computed by a plain recursive count over
                 the declaration, then compared with how many the walk produced.
    Placed       Is each route filed under the capability whose decisions it actually walks?

The report is data, not prose: counts and specific findings, so a page can render it and a test
can assert on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .graph import DecisionGraph, Span, spans_for
from .models import IntakeData, Scenario

# How deep the independent count follows a route before it gives up and reports the branch as
# uncountable. Nothing to do with the walk's own limits: this is a guard on a recursive count
# over a graph that may contain a cycle, and a branch that reaches it is reported rather than
# quietly dropped.
COUNT_CEILING = 5000


@dataclass
class Finding:
    """One thing wrong with one route, named so it can be acted on."""

    scenario_id: str
    step: int
    problem: str

    def __str__(self) -> str:
        where = f" at step {self.step}" if self.step >= 0 else ""
        return f"{self.scenario_id}{where}: {self.problem}"


@dataclass
class WalkReport:
    """What the check found. Empty lists mean the walk agrees with the declaration."""

    routes: int = 0
    replayed: int = 0
    unreplayable: List[Finding] = field(default_factory=list)
    misplaced: List[Finding] = field(default_factory=list)
    duplicates: List[Finding] = field(default_factory=list)

    outcomes_declared: int = 0
    outcomes_walked: int = 0
    outcomes_missing: List[str] = field(default_factory=list)

    states_declared: int = 0
    states_reached: int = 0
    states_missing: List[str] = field(default_factory=list)

    capabilities_walked: Dict[str, int] = field(default_factory=dict)
    capabilities_empty: List[str] = field(default_factory=list)

    expected_by_capability: Dict[str, Optional[int]] = field(default_factory=dict)
    counted_by_capability: Dict[str, int] = field(default_factory=dict)
    count_disagreements: List[str] = field(default_factory=list)

    @property
    def sound(self) -> bool:
        """Whether every route the walk produced could actually be taken."""
        return not (self.unreplayable or self.misplaced or self.duplicates)

    @property
    def complete(self) -> bool:
        """Whether every declared outcome is exercised by some route."""
        return not self.outcomes_missing

    @property
    def outcome_coverage(self) -> float:
        return self.outcomes_walked / self.outcomes_declared if self.outcomes_declared else 1.0

    @property
    def state_coverage(self) -> float:
        return self.states_reached / self.states_declared if self.states_declared else 1.0

    def headline(self) -> str:
        """One line for a log or a page."""
        return (f"{self.replayed}/{self.routes} routes replay, "
                f"{self.outcomes_walked}/{self.outcomes_declared} outcomes exercised, "
                f"{self.states_reached}/{self.states_declared} states reached")


def check_walk(intake: IntakeData, scenarios: Sequence[Scenario]) -> WalkReport:
    """Everything below, in one call."""
    graph = DecisionGraph(intake.decisions, intake.states)
    walked = [s for s in scenarios if list(getattr(s, "path", ()) or ())]
    report = WalkReport(routes=len(walked))

    _replay(graph, intake, walked, report)
    _coverage(graph, intake, walked, report)
    _placement(intake, walked, report)
    _count(graph, intake, walked, report)
    return report


def opening_of(intake: IntakeData, graph: DecisionGraph, scenario: Scenario) -> Set[str]:
    """Which state(s) a route can have started from.

    Read off the scenario first, because that is where the answer actually is: a route records
    the position it was seeded at. Falling back to the capability's declared entries, and then to
    the graph's own start, covers a route the walk produced for a span it could not place -- which
    is a real case on a declaration whose spans are drawn wrongly, and one this check must not
    mistake for an invented route.
    """
    seeded = (getattr(scenario, "seeded_state", "") or "").strip()
    if seeded:
        named = {s.id for s in intake.states if (s.description or "").strip() == seeded}
        if named:
            return named
    declared = next((set(c.entry_states) for c in intake.capabilities
                     if c.id == scenario.capability_id and c.entry_states), set())
    if declared:
        return declared
    return set(graph.start_states)


# --------------------------------------------------------------------------- replay
def _replay(graph: DecisionGraph, intake: IntakeData, scenarios: Sequence[Scenario],
            report: WalkReport) -> None:
    """Walk each recorded route by hand and confirm every step was available when it was taken."""
    offers = {state.id: {d.upper() for d in state.next_decisions} for state in intake.states}
    terminal = {state.id for state in intake.states if state.is_terminal}
    known = {state.id for state in intake.states}
    exit_of = {c.id: set(c.exit_states) for c in intake.capabilities}
    every_entry = {s for c in intake.capabilities for s in c.entry_states}

    seen_signatures: Dict[tuple, str] = {}

    for scenario in scenarios:
        steps = list(scenario.path)
        ok = True

        # Two routes over the same decisions entered at different positions are two scenarios,
        # not a duplicate -- "after identification by card" and "after identification by code"
        # are the case the span machinery exists to separate. So the opening is part of the
        # signature.
        opening = opening_of(intake, graph, scenario)
        signature = (scenario.capability_id, tuple(sorted(opening)),
                     tuple((s.decision_id, s.variant) for s in steps))
        if signature in seen_signatures:
            report.duplicates.append(Finding(
                scenario.id, -1,
                f"walks the same route from the same position as {seen_signatures[signature]}"))
            ok = False
        else:
            seen_signatures[signature] = scenario.id

        first = steps[0].decision_id.upper()
        if opening and not any(first in offers.get(state, set()) for state in opening):
            report.unreplayable.append(Finding(
                scenario.id, 1,
                f"{steps[0].decision_id} is not offered by any state this route can start at "
                f"({', '.join(sorted(opening))})"))
            ok = False

        fired: Dict[str, int] = {}
        for index, step in enumerate(steps, start=1):
            decision = graph.decision(step.decision_id)
            if decision is None:
                report.unreplayable.append(Finding(
                    scenario.id, index, f"{step.decision_id} is not a declared decision"))
                ok = False
                break
            if step.variant not in decision.variants:
                report.unreplayable.append(Finding(
                    scenario.id, index,
                    f"{step.decision_id} has no declared outcome {step.variant!r}"))
                ok = False
                break

            fired[decision.id] = fired.get(decision.id, 0) + 1
            if fired[decision.id] > decision.max_attempts:
                report.unreplayable.append(Finding(
                    scenario.id, index,
                    f"{decision.id} is taken {fired[decision.id]} times but declares "
                    f"{decision.max_attempts} attempt(s)"))
                ok = False
                break

            landing = step.next_state
            if landing not in known:
                report.unreplayable.append(Finding(
                    scenario.id, index,
                    f"{decision.id}={step.variant} lands on {landing!r}, which is not a "
                    f"declared state"))
                ok = False
                break

            if index < len(steps):
                onward = steps[index].decision_id.upper()
                if onward not in offers.get(landing, set()):
                    report.unreplayable.append(Finding(
                        scenario.id, index + 1,
                        f"{landing} does not offer {steps[index].decision_id}, so this route "
                        f"could not continue"))
                    ok = False
                    break

        if not ok:
            continue

        # Where it stops. A route may end the interaction, hand on at one of its own capability's
        # exits, or hand on at another capability's entry -- the last of which is how a route the
        # walk produced for a span it could not place legitimately finishes. Stopping anywhere
        # else means the route was cut.
        last = steps[-1].next_state
        if last not in terminal and last not in exit_of.get(scenario.capability_id, set()) \
                and last not in every_entry:
            report.unreplayable.append(Finding(
                scenario.id, len(steps),
                f"stops at {last}, which neither ends the interaction nor hands on anywhere "
                f"declared"))
            continue

        report.replayed += 1


# --------------------------------------------------------------------------- coverage
def _coverage(graph: DecisionGraph, intake: IntakeData, scenarios: Sequence[Scenario],
              report: WalkReport) -> None:
    """What the declaration offers against what the routes actually touch."""
    declared_outcomes = {(d.id, v) for d in intake.decisions if not d.out_of_scope
                         for v in d.variants}
    walked_outcomes = {(s.decision_id, s.variant) for scenario in scenarios
                       for s in scenario.path}
    report.outcomes_declared = len(declared_outcomes)
    report.outcomes_walked = len(declared_outcomes & walked_outcomes)
    report.outcomes_missing = sorted(f"{d}={v}" for d, v in declared_outcomes - walked_outcomes)

    # Every state a route passes through, including the one it started from.
    reached: Set[str] = set()
    for scenario in scenarios:
        reached |= {step.next_state for step in scenario.path}
        reached |= opening_of(intake, graph, scenario)

    declared_states = {s.id for s in intake.states}
    report.states_declared = len(declared_states)
    report.states_reached = len(declared_states & reached)
    report.states_missing = sorted(declared_states - reached)

    counts: Dict[str, int] = {c.id: 0 for c in intake.capabilities}
    for scenario in scenarios:
        if scenario.capability_id in counts:
            counts[scenario.capability_id] += 1
    report.capabilities_walked = counts
    report.capabilities_empty = sorted(k for k, n in counts.items() if not n)


# --------------------------------------------------------------------------- placement
def _placement(intake: IntakeData, scenarios: Sequence[Scenario], report: WalkReport) -> None:
    """Whether a route is filed under a capability whose decisions it actually walks.

    Not an error where a route legitimately crosses a boundary -- it is reported where the route
    walks *no* decision of the capability it claims, which means the space is filed wrongly and
    every per-capability count read off it is wrong too.
    """
    owner = {d.id: d.trigger_capability for d in intake.decisions}
    declared = {c.id for c in intake.capabilities}
    for scenario in scenarios:
        claimed = scenario.capability_id
        # Only a route claiming a *declared* capability can be misfiled. A route the walk produced
        # for a region no capability covers is filed under no capability by construction, and
        # saying so would report the declaration's gap as the walk's error.
        if not claimed or claimed not in declared:
            continue
        walked = {owner.get(step.decision_id, "") for step in scenario.path}
        if claimed not in walked:
            report.misplaced.append(Finding(
                scenario.id, -1,
                f"filed under {claimed} but walks no decision tagged with it "
                f"(walks {', '.join(sorted(w for w in walked if w)) or 'nothing tagged'})"))


# --------------------------------------------------------------------------- independent count
def _count(graph: DecisionGraph, intake: IntakeData, scenarios: Sequence[Scenario],
           report: WalkReport) -> None:
    """How many routes the declaration allows, counted without walking it.

    A plain recursion over the declaration: from a state, the number of routes is the sum over
    every decision it offers and every outcome of that decision of the routes onward. It shares
    no code with the enumeration, so agreement between the two is evidence rather than tautology.

    Reported per capability and never silently. A branch that cannot be counted -- a cycle, or one
    deeper than the ceiling -- makes the expectation ``None`` for that capability rather than a
    number that is quietly wrong.
    """
    for capability, per_span in _spans_by_capability(graph, intake).items():
        expected: Optional[int] = 0
        for span in per_span:
            reachable = _routes_from(graph, span, span.entry_state, {}, 0)
            if reachable is None:
                expected = None
                break
            expected += reachable
        report.expected_by_capability[capability] = expected

    counted: Dict[str, int] = {}
    for scenario in scenarios:
        counted[scenario.capability_id] = counted.get(scenario.capability_id, 0) + 1
    report.counted_by_capability = counted

    for capability, expected in report.expected_by_capability.items():
        actual = counted.get(capability, 0)
        if expected is None:
            report.count_disagreements.append(
                f"{capability or 'the whole graph'}: could not be counted independently "
                f"(the declaration loops or is deeper than {COUNT_CEILING}); the walk produced "
                f"{actual}")
        elif expected != actual:
            report.count_disagreements.append(
                f"{capability or 'the whole graph'}: the declaration allows {expected} route(s), "
                f"the walk produced {actual}")


def _spans_by_capability(graph: DecisionGraph,
                         intake: IntakeData) -> Dict[str, List[Span]]:
    found: Dict[str, List[Span]] = {}
    for span in spans_for(graph, intake.capabilities):
        found.setdefault(span.capability_id, []).append(span)
    return found


def _routes_from(graph: DecisionGraph, span: Span, state_id: str,
                 fired: Dict[str, int], depth: int) -> Optional[int]:
    """Routes from this position to a stop, counted rather than listed."""
    if depth > COUNT_CEILING:
        return None
    state = graph.state(state_id)
    if state is None:
        return 0
    if state_id in span.exit_states or state.is_terminal:
        return 1 if depth else 0

    offered = [graph.decision(d) for d in state.next_decisions]
    live = [d for d in offered if d is not None and not d.out_of_scope
            and fired.get(d.id, 0) < d.max_attempts]
    if not live:
        # Every way on is out of scope: the route stops here and is kept, exactly as the walk
        # keeps it. Out of attempts is not the same case and contributes nothing.
        blocked = [d for d in offered if d is not None and d.out_of_scope]
        return 1 if blocked and len(blocked) == len([d for d in offered if d is not None]) else 0

    total = 0
    for decision in live:
        occurrence = fired.get(decision.id, 0)
        for variant in decision.variants:
            onward = _routes_from(graph, span, graph.successor(decision.id, variant, occurrence),
                                  {**fired, decision.id: occurrence + 1}, depth + 1)
            if onward is None:
                return None
            total += onward
    return total
