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

        self.start_states = [s.id for s in states if _START_HINT.search(s.reached_via or "")]
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
    """DFS from every start state to every terminal (or undeclared) state."""
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
            if path:
                paths.append(list(path))
            return

        advanced = False
        for decision_id in state.next_decisions:
            decision = graph.decision(decision_id)
            if decision is None or decision.out_of_scope:
                continue
            occurrence = fired.get(decision_id, 0)
            if occurrence >= decision.max_attempts:
                continue
            advanced = True
            for variant in decision.variants:
                next_state = graph.successor(decision_id, variant, occurrence)
                visit(next_state,
                      path + [Step(decision_id, variant, next_state)],
                      {**fired, decision_id: occurrence + 1})

        # Every decision this state offers is either out of scope or has used up its attempts.
        # The interaction stops here, which is a real outcome either way -- exhausting a retry
        # limit is usually exactly what the owner declared Max Attempts to bound, and a decision
        # marked out of scope is one this review is deliberately not walking into -- so the path
        # is recorded rather than discarded.
        if not advanced and path:
            paths.append(list(path))

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


def augment_variants(graph: DecisionGraph, paths: List[Path]) -> List[Path]:
    """A focused path for every declared (decision, variant) the DFS missed."""
    already = covered_variants(paths)
    extra: List[Path] = []
    for decision in graph.decisions.values():
        if decision.out_of_scope:
            continue
        for variant in decision.variants:
            if (decision.id, variant) in already:
                continue
            prefix = _shortest_prefix_to(graph, decision.id)
            extra.append(prefix + [Step(decision.id, variant, graph.successor(decision.id, variant))])
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
