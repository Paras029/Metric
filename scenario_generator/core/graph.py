"""Decision graph (L3 joined to L4) and exhaustive path enumeration over it.

Nodes are states; an edge is a (decision, variant) pair moving from one state to the
next. A variant with no declared destination state simply ends the path there.

Enumeration is two passes: a DFS from every start state, then a focused path for any
declared (decision, variant) the DFS never exercised.

Loops are bounded by each decision's own declared `Max Attempts`, not by a global revisit cap,
so a decision that genuinely allows three tries produces three-try paths. MAX_DEPTH and
MAX_PATHS remain as backstops only, and hitting either is reported rather than silent.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple

from ..utils.text import parse_reached_via
from .models import Decision, State, Step

logger = logging.getLogger(__name__)

MAX_DEPTH = 12
MAX_PATHS = 1000

# What an opening state says in its "Reached Via" cell when it is not reached by a decision at all.
# Only consulted for states that name no decision edge: a state reached via "DEC-02=Reconnected"
# is reached by a decision, and matching "connect" inside that outcome would make it a second
# place the walk starts from and fill the scenario space with routes the agent cannot take.
_START_HINT = re.compile(r"start|begin|connect|inbound", re.I)

Path = List[Step]


class DecisionGraph:
    def __init__(self, decisions: List[Decision], states: List[State]) -> None:
        self.decisions: Dict[str, Decision] = {d.id: d for d in decisions}
        self.states: Dict[str, State] = {s.id: s for s in states}

        self._edges: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        for state in states:
            for edge in parse_reached_via(state.reached_via):
                self._edges[edge].append(state.id)

        self.start_states = [s.id for s in states
                             if not parse_reached_via(s.reached_via)
                             and _START_HINT.search(s.reached_via or "")]
        if not self.start_states and states:
            self.start_states = [states[0].id]

    def state(self, state_id: str) -> Optional[State]:
        return self.states.get(state_id)

    def decision(self, decision_id: str) -> Optional[Decision]:
        return self.decisions.get(decision_id)

    def successor(self, decision_id: str, variant: str, occurrence: int = 0) -> str:
        """State reached by a (decision, variant); a synthetic leaf id if none is declared."""
        candidates = self._edges.get((decision_id, variant), [])
        if candidates:
            return candidates[min(occurrence, len(candidates) - 1)]
        return f"OUT:{decision_id}={variant}"

    def states_offering(self, decision_id: str) -> List[str]:
        return [s.id for s in self.states.values() if decision_id in s.next_decisions]


# --------------------------------------------------------------------------- enumeration
def walk_paths(graph: DecisionGraph) -> List[Path]:
    """DFS from every start state, keeping only the routes that reach a declared ending.

    A route finishes when it arrives at a state the intake marks as ending the interaction. Every
    other way a walk can come to a halt is the declaration running out rather than the agent
    finishing:

    * the outcome taken names a destination no state declares (``OUT:DEC-02=Odd``);
    * the state reached leads nowhere but is not marked as an ending;
    * every decision the state offers has used up its ``Max Attempts``, or is out of scope.

    All three used to be recorded as paths, and became scenarios. None of them can be one: a
    scenario is a conversation issued to the model owner with an expected outcome behind it, and a
    route the declaration stops short of has no expected outcome to have -- so the metadata
    workbook carried an ending of nothing at all, and the pack asked for a conversation nobody
    could mark. They are dropped here, which is what keeps that out of everything downstream.
    """
    paths: List[Path] = []
    truncated = {"paths": False, "depth": False}

    def visit(state_id: str, path: Path, fired: Dict[str, int]) -> None:
        if len(paths) >= MAX_PATHS:
            truncated["paths"] = True
            return
        if len(path) > MAX_DEPTH:
            truncated["depth"] = True
            return
        state = graph.state(state_id)
        if state is None or state.is_terminal or not state.next_decisions:
            if path and state is not None and state.is_terminal:
                paths.append(list(path))
            return

        for decision_id in state.next_decisions:
            decision = graph.decision(decision_id)
            if decision is None or decision.out_of_scope:
                continue
            occurrence = fired.get(decision_id, 0)
            if occurrence >= decision.max_attempts:
                continue
            for variant in decision.variants:
                next_state = graph.successor(decision_id, variant, occurrence)
                visit(next_state,
                      path + [Step(decision_id, variant, next_state)],
                      {**fired, decision_id: occurrence + 1})

        # Nothing is recorded where every decision on offer is out of scope or out of attempts.
        # Exhausting a retry limit is a real thing that happens, but what the agent does at that
        # point is exactly what the intake has not said -- so there is no outcome to test against.

    for start in graph.start_states:
        visit(start, [], {})

    if truncated["paths"]:
        logger.warning("Path enumeration stopped at the MAX_PATHS cap of %d — the scenario set is "
                       "incomplete. Split the use case or raise the cap.", MAX_PATHS)
    if truncated["depth"]:
        logger.warning("One or more paths were cut at the MAX_DEPTH limit of %d steps — those "
                       "branches are not represented.", MAX_DEPTH)
    return paths


def covered_variants(paths: List[Path]) -> Set[Tuple[str, str]]:
    return {(step.decision_id, step.variant) for path in paths for step in path}


def _shortest_prefix_to(graph: DecisionGraph, decision_id: str) -> Path:
    """BFS for the shortest path from a start state to any state offering `decision_id`."""
    targets = set(graph.states_offering(decision_id))
    if not targets:
        return []
    queue = deque((start, []) for start in graph.start_states)
    visited = set(graph.start_states)
    while queue:
        state_id, prefix = queue.popleft()
        if state_id in targets:
            return prefix
        state = graph.state(state_id)
        if state is None or state.is_terminal or len(prefix) >= MAX_DEPTH:
            continue
        for next_decision in state.next_decisions:
            decision = graph.decision(next_decision)
            if decision is None or decision.out_of_scope:
                continue
            for variant in decision.variants:
                next_state = graph.successor(next_decision, variant)
                if next_state in visited:
                    continue
                visited.add(next_state)
                queue.append((next_state, prefix + [Step(next_decision, variant, next_state)]))
    return []


def _shortest_suffix_to_ending(graph: DecisionGraph, state_id: str,
                               fired: Dict[str, int], taken: int) -> Optional[Path]:
    """BFS onward from `state_id` to any declared ending, or ``None`` if none can be reached.

    Attempt counts are carried in rather than restarted, because they are what makes the rest of
    the route legal: a suffix that fires a decision a fourth time is not a route the agent has.
    """
    if graph.state(state_id) is not None and graph.state(state_id).is_terminal:
        return []

    queue = deque([(state_id, [], fired)])
    seen = {(state_id, tuple(sorted(fired.items())))}
    while queue:
        current, suffix, used = queue.popleft()
        state = graph.state(current)
        if state is None or taken + len(suffix) >= MAX_DEPTH:
            continue
        for decision_id in state.next_decisions:
            decision = graph.decision(decision_id)
            if decision is None or decision.out_of_scope:
                continue
            occurrence = used.get(decision_id, 0)
            if occurrence >= decision.max_attempts:
                continue
            for variant in decision.variants:
                next_state = graph.successor(decision_id, variant, occurrence)
                step = Step(decision_id, variant, next_state)
                landed = graph.state(next_state)
                if landed is not None and landed.is_terminal:
                    return suffix + [step]
                after = {**used, decision_id: occurrence + 1}
                mark = (next_state, tuple(sorted(after.items())))
                if landed is None or mark in seen:
                    continue
                seen.add(mark)
                queue.append((next_state, suffix + [step], after))
    return None


def augment_variants(graph: DecisionGraph, paths: List[Path]) -> List[Path]:
    """A focused route for every declared (decision, variant) the DFS missed.

    Carried on to an ending rather than stopped at the outcome being reached for. These exist to
    exercise an outcome the exhaustive walk could not get to -- usually one behind a retry limit --
    and stopping the moment it fires made every one of them a route with no declared ending, which
    is the one thing a scenario cannot be. An outcome whose continuation dead-ends is dropped: it
    is unreachable in a complete route, so there is no conversation to ask anybody to run.
    """
    already = covered_variants(paths)
    extra: List[Path] = []
    for decision in graph.decisions.values():
        if decision.out_of_scope:
            continue
        missing = [v for v in decision.variants if (decision.id, v) not in already]
        if not missing:
            continue
        # One search per decision, not per outcome: the route *to* a decision is the same
        # whichever of its outcomes is being reached for.
        prefix = _shortest_prefix_to(graph, decision.id)
        fired: Dict[str, int] = {}
        for step in prefix:
            fired[step.decision_id] = fired.get(step.decision_id, 0) + 1
        for variant in missing:
            occurrence = fired.get(decision.id, 0)
            landing = graph.successor(decision.id, variant, occurrence)
            reached = prefix + [Step(decision.id, variant, landing)]
            suffix = _shortest_suffix_to_ending(
                graph, landing, {**fired, decision.id: occurrence + 1}, len(reached))
            if suffix is None:
                continue
            extra.append(reached + suffix)
            already.add((decision.id, variant))
    return extra


def enumerate_paths(graph: DecisionGraph) -> Tuple[List[Path], List[Path]]:
    """Return (walked, augmented) paths, de-duplicated by decision-variant signature."""
    walked = walk_paths(graph)
    seen: Set[tuple] = set()

    def keep(path: Path) -> bool:
        signature = tuple((s.decision_id, s.variant) for s in path)
        if not signature or signature in seen:
            return False
        seen.add(signature)
        return True

    unique_walked = [p for p in walked if keep(p)]
    unique_augmented = [p for p in augment_variants(graph, walked) if keep(p)]
    return unique_walked, unique_augmented


# --------------------------------------------------------------------------- structural hints
def _landing(graph: DecisionGraph, decision: Decision, variant: str) -> tuple:
    """Where one outcome of one decision ends up, as a value two decisions can be compared by.

    A terminal landing is its outcome type; a continuing one is the set of decisions reachable
    from there. Either way, two outcomes with the same landing put the interaction in the same
    place afterwards regardless of which one was taken -- which is the one fact that makes a pair
    of decisions a *candidate* for :func:`convergence_candidates`, not a judgement that they
    should merge.
    """
    state = graph.state(graph.successor(decision.id, variant))
    if state is None:
        return ("undeclared",)
    if state.is_terminal:
        return ("terminal", state.outcome_type)
    return ("continue", tuple(sorted(state.next_decisions)))


def convergence_candidates(graph: DecisionGraph) -> List[List[str]]:
    """Decisions whose every outcome lands on the same downstream point(s) as each other's.

    Purely structural, and deliberately not a recommendation: it says that after taking any
    outcome of any decision in a group, the interaction continues (or ends) identically regardless
    of which decision or outcome produced it -- which is the shape a "different routes to the same
    fact" consolidation needs, not proof that collapsing the group is a good idea. A decision that
    declares no outcomes, or whose destination was never declared, is excluded: nothing about an
    undeclared landing can be compared.
    """
    signatures: Dict[str, frozenset] = {}
    for decision in graph.decisions.values():
        if not decision.variants or decision.out_of_scope:
            continue
        landings = {_landing(graph, decision, variant) for variant in decision.variants}
        if ("undeclared",) in landings:
            continue
        signatures[decision.id] = frozenset(landings)

    groups: Dict[frozenset, List[str]] = {}
    for decision_id, signature in signatures.items():
        groups.setdefault(signature, []).append(decision_id)
    return [sorted(ids) for ids in groups.values() if len(ids) > 1]
