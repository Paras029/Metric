"""Decision graph (L3 joined to L4) and exhaustive path enumeration over it.

Nodes are states; an edge is a (decision, variant) pair moving from one state to the
next. A variant with no declared destination state simply ends the path there.

Enumeration is two passes: a DFS from every start state, then a focused path for any
declared (decision, variant) the DFS never exercised.

Loops are bounded by each decision's own declared `Max Attempts`, not by a global revisit cap,
so a decision that genuinely allows three tries produces three-try paths. A route that *returns*
to a decision after going elsewhere is deliberately not enumerated here and belongs in the
variation space -- see the note above the loop rule. Nothing truncates the walk: a route's
length is bounded by the declaration's own attempt counts, and completeness is judged by
:mod:`core.verify` rather than guessed at from a cap.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from ..utils.text import parse_reached_via
from .models import Capability, Decision, State, Step

logger = logging.getLogger(__name__)

# A route that leaves a decision and comes back to it is **not enumerated**, and that is a choice
# rather than an oversight.
#
# It is a different thing from a retry. Max Attempts says how often the agent may try one decision
# on the spot -- three goes at an identity check -- and the walk honours that. A *return* is the
# flow genuinely going elsewhere and coming back: a fallback that could not understand the request
# and routes to the start of the block, a verification step that sends the conversation back to be
# identified again. The intake says nothing about how often that may happen, so any bound on it
# would be this tool's invention rather than the declaration's.
#
# Walking them multiplies the space by every loop in the graph, and what the extra routes test is
# the same behaviour a second time from a different distance. That is a *variation* on a scenario,
# not a scenario, and it belongs in the variation space where the number of laps is a knob rather
# than in the base space where it is a combinatorial explosion nobody asked for.
#
# There is no depth limit and no path cap.
#
# Both used to exist as backstops and both were doing harm. A route's length is already bounded by
# the declaration: every decision may fire at most its declared Max Attempts times, so the longest
# route is the sum of those and the walk terminates without help. A cap on top of that arithmetic
# can only cut a route the declaration allows, which is the one thing enumeration must not do.
#
# The path cap went for the same reason. Scoping the walk to a capability keeps each block's route
# count small, and returning routes are left to the variation space, so the combinatorial blow-up
# a cap was guarding against does not arise. A declaration that genuinely produces more routes
# than anybody can run is a fact about the declaration and belongs in the report, not in a silent
# truncation.

_START_HINT = re.compile(r"start|begin|connect|inbound", re.I)

Path = List[Step]


@dataclass
class Limits:
    """Kept for callers that thread it through. Nothing truncates the walk any more, so it stays
    empty; :func:`core.verify.check_walk` is where a scenario space is now judged complete."""

    paths: bool = False
    depth: bool = False

    @property
    def truncated(self) -> bool:
        return False


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


# --------------------------------------------------------------------------- spans
@dataclass(frozen=True)
class Span:
    """One block of the graph to enumerate over, entered one way.

    A span is a capability and a single entry state, so a capability that can be entered two ways
    produces two spans -- which is what makes "after identification by document" and "after
    identification by one-time code" two scenario sets rather than one set that quietly assumes
    the first. See :func:`spans_for`.
    """

    capability_id: str
    name: str
    entry_state: str
    exit_states: FrozenSet[str]
    adopts_orphans: bool = True
    """Whether a decision no state leads to, tagged with this capability, is walked here.

    A disconnected decision is still declared, and its outcomes are still things the agent can do,
    so the augmentation pass gives it a route of its own rather than leaving it untested. It has
    no position in the graph, so nothing about the graph says which block it belongs to -- the
    capability it is tagged with does. Set on the first span of each capability only, or a
    capability entered two ways would produce the same orphan route twice.
    """

    @property
    def is_whole_graph(self) -> bool:
        return not self.capability_id


def spans_for(graph: DecisionGraph, capabilities: Sequence[Capability]) -> List[Span]:
    """What to walk: one span per capability entry state, or the whole graph if none are drawn.

    The fallback matters as much as the feature. An intake nobody has divided into capabilities
    yet is the ordinary state of a use case on its first run, and a scenario space of nothing at
    all would read as the tool being broken rather than as a step not yet taken. So an undivided
    graph walks exactly as it always did, start to ending, and dividing it is what makes the
    enumeration smaller.

    Entry and exit states that name nothing in the graph are dropped rather than honoured: a span
    hanging off a state id somebody mistyped would silently enumerate nothing, and a capability
    that contributes no scenarios is the failure this is most likely to produce by accident.
    """
    spans: List[Span] = []
    for capability in capabilities:
        if capability.out_of_scope:
            continue
        entries = [s for s in capability.entry_states if s in graph.states]
        exits = frozenset(s for s in capability.exit_states if s in graph.states)
        if not entries or not exits:
            if capability.entry_states or capability.exit_states:
                logger.warning(
                    "Capability %s declares a span this graph cannot place (entry %s, exit %s), "
                    "so it is not walked as a block.", capability.id,
                    ", ".join(capability.entry_states) or "none",
                    ", ".join(capability.exit_states) or "none")
            continue
        for position, entry in enumerate(entries):
            spans.append(Span(capability.id, capability.name or capability.id, entry, exits,
                              adopts_orphans=position == 0))

    # The whole-graph fallback is for a declaration nobody has divided up yet. A declaration where
    # every block was divided up and then put out of scope has said something else entirely, and
    # walking it end to end would test exactly what it asked not to be tested.
    if not spans and not any(c.out_of_scope for c in capabilities):
        endings = frozenset(s.id for s in graph.states.values() if s.is_terminal)
        return [Span("", "", start, endings) for start in graph.start_states]
    if not spans:
        return []

    return spans + _spans_for_the_gaps(graph, spans)


UNASSIGNED = "UNASSIGNED"
"""The block a decision belonging to no capability is walked under.

Not a capability, and not pretending to be one: it has no entry or exit anybody drew and no name
anybody chose. It exists because the alternative is worse -- a decision in no capability is walked
by nothing, so every one of its outcomes goes untested and nothing anywhere says so. A consent gate
between identification and verification is a real branch of a live system whether or not somebody
got round to filing it under a heading.
"""


def _spans_for_the_gaps(graph: DecisionGraph, drawn: Sequence[Span]) -> List[Span]:
    """Spans covering the decisions no capability's span reaches.

    Capabilities are drawn by hand, so between two of them there is usually something nobody
    filed: a consent gate, a channel check, a step that belongs to the flow rather than to any one
    block. Those decisions are declared, their outcomes are real, and without this they are walked
    by nothing at all -- the quietest possible failure, since the declaration looks complete and
    the scenario space simply has a hole in it.

    Each gap span starts where the uncovered region is entered from -- a capability's exit, or the
    graph's own start -- and ends where the flow rejoins a capability or stops. That is the same
    rule the drawn spans follow, so a scenario out of a gap reads like any other: it starts
    somewhere stated and ends somewhere declared.
    """
    covered: Set[str] = set()
    for span in drawn:
        covered |= _decisions_within(graph, span)

    loose = {d.id for d in graph.decisions.values()
             if not d.out_of_scope and d.id not in covered and graph.states_offering(d.id)}
    if not loose:
        return []

    entries = {span.entry_state for span in drawn}
    endings = frozenset(s.id for s in graph.states.values() if s.is_terminal)
    exits = frozenset(entries | set(endings))

    # Entered from wherever the covered part of the graph hands into it. A state that offers a
    # loose decision and is itself only reachable through other loose decisions is in the middle
    # of the gap rather than at its edge, and walking from there as well would re-walk the tail of
    # a route the edge already covers.
    from_inside = {graph.successor(decision, variant)
                   for decision in loose
                   for variant in graph.decisions[decision].variants}
    openings = [state.id for state in graph.states.values()
                if any(d in loose for d in state.next_decisions)
                and state.id not in from_inside]
    if not openings:                       # every opening is inside the gap: start where it starts
        openings = sorted({state.id for state in graph.states.values()
                           if any(d in loose for d in state.next_decisions)})[:1]

    logger.info("%d decision(s) belong to no capability (%s); walking them from %s so their "
                "outcomes are still tested.", len(loose), ", ".join(sorted(loose)),
                ", ".join(sorted(openings)))
    return [Span(UNASSIGNED, "Not grouped into a capability", opening,
                 frozenset(exits - {opening}), adopts_orphans=index == 0)
            for index, opening in enumerate(sorted(openings))]


# --------------------------------------------------------------------------- enumeration
def walk_paths(graph: DecisionGraph, span: Optional[Span] = None,
               limits: Optional["Limits"] = None) -> List[Path]:
    """DFS across one span, keeping only the routes that reach one of its exits.

    A route finishes when it arrives at a state the span names as an exit, or at a state the
    intake marks as ending the interaction -- an ending inside a block is an ending, whether or
    not somebody remembered to list it. Every other way a walk can come to a halt is the
    declaration running out rather than the agent finishing:

    * the outcome taken names a destination no state declares (``OUT:DEC-02=Odd``);
    * the state reached leads nowhere and is neither an exit nor an ending;
    * every decision the state offers has used up its ``Max Attempts``.

    All three used to be recorded as paths, and became scenarios. None of them can be one: a
    scenario is a conversation issued to the model owner with an expected outcome behind it, and a
    route the declaration stops short of has no expected outcome to have -- so the metadata
    workbook carried an ending of nothing at all, and the pack asked for a conversation nobody
    could mark. They are dropped here, which is what keeps that out of everything downstream.

    **An out-of-scope decision is not one of those.** It is a boundary somebody drew deliberately
    -- a sub-system reviewed under a separate engagement -- so a route arriving at one has an
    expected outcome: the agent hands off. Those routes finish there and are kept, which is what
    makes the flag mean "do not test past here" rather than "do not test anything that leads
    here".

    Called with no span, this walks the whole graph exactly as it did before spans existed.
    """
    if span is None:
        endings = frozenset(s.id for s in graph.states.values() if s.is_terminal)
        return [p for start in graph.start_states
                for p in walk_paths(graph, Span("", "", start, endings), limits)]

    paths: List[Path] = []

    def finishes_here(state: Optional[State], state_id: str) -> bool:
        return state_id in span.exit_states or (state is not None and state.is_terminal)

    def visit(state_id: str, path: Path, fired: Dict[str, int]) -> None:
        state = graph.state(state_id)
        if state is None or finishes_here(state, state_id) or not state.next_decisions:
            if path and finishes_here(state, state_id):
                paths.append(list(path))
            return

        # A boundary somebody drew, rather than the declaration running out. Where every decision
        # on offer is marked out of scope, the route ends here and is kept: that is the whole
        # point of marking something out of scope -- a plug-and-play sub-system reviewed under a
        # separate engagement, whose behaviour is not being tested but whose *hand-off* is. Left
        # unrecorded, everything on the way to that boundary went untested too, which is the
        # opposite of what the flag is for.
        #
        # Retry exhaustion is deliberately not this case. There the intake genuinely has not said
        # what the agent does next, so there is no expected outcome to issue.
        offered = [graph.decision(d) for d in state.next_decisions]
        if any(d is not None for d in offered) and all(
                d is None or d.out_of_scope for d in offered):
            if path:
                paths.append(list(path))
            return

        for decision_id in state.next_decisions:
            decision = graph.decision(decision_id)
            if decision is None or decision.out_of_scope:
                continue
            occurrence = fired.get(decision_id, 0)
            # Max Attempts bounds both taking a decision again on the spot and coming back to it
            # after going elsewhere. The second of those is a route the declaration does draw and
            # this does not walk -- see the note above for why it is left to the
            # variation space rather than multiplied into this one.
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

    visit(span.entry_state, [], {})

    return paths


def _longest(graph: DecisionGraph) -> int:
    """The longest route the declaration allows: every decision fired its full attempt count.

    Not a cap. The focused searches of :func:`augment_variants` are breadth-first over the same
    graph and need a stopping condition, and this is the one the declaration itself states.
    """
    return max(1, sum(d.max_attempts for d in graph.decisions.values()))


def covered_variants(paths: List[Path]) -> Set[Tuple[str, str]]:
    return {(step.decision_id, step.variant) for path in paths for step in path}


def _belongs_to(graph: DecisionGraph, decision: Decision, span: Span, within: Set[str]) -> bool:
    """Whether this span is the one that should give this decision a focused route."""
    if decision.id in within:
        return True
    if not span.adopts_orphans or graph.states_offering(decision.id):
        return False
    # Disconnected: nothing in the graph reaches it, so the capability it names is the only
    # statement anybody has made about where it belongs.
    return span.is_whole_graph or decision.trigger_capability == span.capability_id


def _decisions_within(graph: DecisionGraph, span: Span) -> Set[str]:
    """Every decision the span can reach, stopping where the block hands on.

    What makes a decision part of a block is that a route through the block can fire it, and the
    walk itself is not enough to tell: a decision behind a retry limit is inside the block and
    never appears in a walked path, which is exactly the case the augmentation pass exists for.
    Reachability ignores attempt counts for that reason -- it answers "does this belong here",
    not "was this walked".
    """
    within: Set[str] = set()
    seen = {span.entry_state}
    queue = deque([span.entry_state])
    while queue:
        state = graph.state(queue.popleft())
        if state is None or state.is_terminal:
            continue
        for decision_id in state.next_decisions:
            decision = graph.decision(decision_id)
            if decision is None or decision.out_of_scope:
                continue
            within.add(decision_id)
            for variant in decision.variants:
                landing = graph.successor(decision_id, variant)
                if landing in seen or landing in span.exit_states:
                    continue
                seen.add(landing)
                queue.append(landing)
    return within


def decisions_owned_by(graph: DecisionGraph, capability_id: str,
                       entry_states: Sequence[str],
                       decisions: Sequence[Decision]) -> Set[str]:
    """Which decisions a capability holds: the ones tagged with it, plus the untagged ones it
    reaches.

    Tagging is the basis rather than reachability, and the difference matters. A walk from
    verification's entry that follows a route back into identification reaches identification's
    decisions too; counting those as verification's would make a return into another block look
    like ordinary branching inside this one, and every span derived from it would be drawn too
    wide. A decision tagged with nothing is the exception -- it is reachable from here and nobody
    has claimed it, so this is the only claim anybody has made.
    """
    tagged = {d.id for d in decisions if d.trigger_capability}
    mine = {d.id for d in decisions if d.trigger_capability == capability_id}
    for entry in entry_states:
        if entry not in graph.states:
            continue
        span = Span(capability_id, capability_id, entry, frozenset())
        mine |= {d for d in _decisions_within(graph, span) if d not in tagged}
    return mine


def states_inside(graph: DecisionGraph, entry_states: Sequence[str], owned: Set[str]) -> Set[str]:
    """The positions a route is still inside the block at: its entries, and anywhere its own
    decisions can be taken from."""
    inside = {s for s in entry_states if s in graph.states}
    for decision_id in owned:
        inside.update(graph.states_offering(decision_id))
    return inside


def exits_for(graph: DecisionGraph, capability_id: str, entry_states: Sequence[str],
              decisions: Sequence[Decision]) -> List[str]:
    """Where a route entering here leaves, worked out from the graph rather than asked for.

    This is the arithmetic that makes a capability's exits something the tool can supply instead
    of something a person types. Walk what the block owns; every state one of its decisions lands
    on that the block is not still inside is a way out -- an ending, a hand-off to the next block,
    or a route back into an earlier one.

    It is a proposal, never an imposition. Two cases give a wrong answer and both are real: a
    block whose decisions are also reachable from outside it, and a validator deliberately drawing
    a block to stop earlier than the graph implies. So the interface shows what this returns and
    lets it be overridden, rather than deriving silently on save.

    Sorted, because it is offered to a person and an arbitrary order reads as a mistake.
    """
    # No entry, no span, nothing to derive. The tagged decisions are still there and would still
    # produce a plausible-looking list, which is exactly the trap: a proposal for a block whose
    # boundary nobody has drawn is a guess dressed as arithmetic.
    if not [s for s in entry_states if s in graph.states]:
        return []

    owned = decisions_owned_by(graph, capability_id, entry_states, decisions)
    inside = states_inside(graph, entry_states, owned)

    found: Set[str] = set()
    for decision_id in owned:
        decision = graph.decision(decision_id)
        if decision is None:
            continue
        for variant in decision.variants:
            landing = graph.state(graph.successor(decision_id, variant))
            if landing is not None and (landing.is_terminal or landing.id not in inside):
                found.add(landing.id)
    return sorted(found)


@dataclass(frozen=True)
class Unreached:
    """An ending a block can produce that no route through the block arrives at."""

    capability_id: str
    state_id: str
    reason: str


def _reachable(graph: DecisionGraph, entry: str, stop_at: FrozenSet[str]) -> Dict[str, int]:
    """States reachable from one entry, with the fewest decisions it takes to get to each.

    Attempt limits are ignored on purpose. This answers "can a route get there at all", and a
    decision behind a retry bound is still somewhere a route can arrive -- the augmentation pass
    exists to reach exactly those. What is *not* ignored is where the block hands on, which is the
    difference the caller reads.
    """
    seen = {entry: 0}
    queue = deque([entry])
    while queue:
        state_id = queue.popleft()
        state = graph.state(state_id)
        if state is None:
            continue
        if state_id != entry and (state.is_terminal or state_id in stop_at):
            continue
        for decision_id in state.next_decisions:
            decision = graph.decision(decision_id)
            if decision is None or decision.out_of_scope:
                continue
            for variant in decision.variants:
                landing = graph.successor(decision_id, variant)
                if landing and landing not in seen:
                    seen[landing] = seen[state_id] + 1
                    queue.append(landing)
    return seen


def endings_not_reached(graph: DecisionGraph, capabilities: Sequence[Capability],
                        decisions: Sequence[Decision]) -> List[Unreached]:
    """Endings a block's own decisions can produce that no route through the block ends at.

    Enumeration is silent about what it could not reach, and that silence is the problem this
    answers. A pack that is missing the ending nobody could get to looks exactly like a pack that
    is complete, and the person reading it has no way to tell whether the declaration is wrong,
    the span is drawn wrong, or the walk is. Naming the ending and the reason turns a suspicion
    into something to act on.

    Three reasons, and they want different fixes:

    - **The decision that produces it cannot be reached from where the block is entered.** The
      declaration is incomplete, or the block is entered somewhere other than where it starts.
    - **The block is declared to hand on before it gets there.** The span is drawn too narrow:
      an exit sits on the way to the ending, and the walk stops at an exit by definition.
    - **The route to it is longer than the depth backstop.** Nothing is wrong with the
      declaration; the enumeration is genuinely incomplete and says so.

    Answered by reachability rather than by enumerating, so it costs two searches per block and
    can be shown on a page that redraws. Retry bounds are ignored for the same reason they are
    ignored in :func:`_decisions_within`: an ending only reachable past one is reached by the
    augmentation pass, so reporting it here would be reporting a route that does get walked.
    """
    found: List[Unreached] = []
    by_capability: Dict[str, List[Span]] = {}
    for span in spans_for(graph, capabilities):
        by_capability.setdefault(span.capability_id, []).append(span)

    for capability_id, spans in by_capability.items():
        wanted: Set[str] = set()
        honouring: Dict[str, int] = {}
        ignoring: Dict[str, int] = {}
        exits: Set[str] = set()
        for span in spans:
            owned = decisions_owned_by(graph, capability_id, [span.entry_state], decisions)
            for decision_id in owned:
                decision = graph.decision(decision_id)
                if decision is None or decision.out_of_scope:
                    continue
                for variant in decision.variants:
                    landing = graph.state(graph.successor(decision_id, variant))
                    if landing is not None and landing.is_terminal:
                        wanted.add(landing.id)
            exits |= set(span.exit_states)
            for where, into in ((span.exit_states, honouring), (frozenset(), ignoring)):
                for state_id, depth in _reachable(graph, span.entry_state, frozenset(where)).items():
                    if state_id not in into or depth < into[state_id]:
                        into[state_id] = depth

        for ending in sorted(wanted):
            if ending in honouring:
                continue
            if ending not in ignoring:
                reason = ("the decision that produces it cannot be reached from where this block "
                          "is entered")
            elif ending not in honouring:
                blocking = sorted(e for e in exits
                                  if e in ignoring and ignoring[e] < ignoring[ending])
                reason = ("the block is declared to hand on at "
                          + (", ".join(blocking) if blocking else "an exit")
                          + " before a route gets there")
            else:                                   # reachable, so a route does arrive there
                continue
            found.append(Unreached(capability_id, ending, reason))
    return found


def entry_candidates(graph: DecisionGraph, capability_id: str,
                     decisions: Sequence[Decision]) -> List[str]:
    """States a route could plausibly enter this capability at: the ones offering a decision it
    is tagged with.

    Not a restriction -- a span may legitimately start anywhere, and the interface keeps the full
    list one click away. It is a shortlist, and on a real declaration that is the difference
    between three rows and twenty-eight.
    """
    mine = [d.id for d in decisions if d.trigger_capability == capability_id]
    found = {state for decision_id in mine for state in graph.states_offering(decision_id)}
    return sorted(found)


def _shortest_prefix_to(graph: DecisionGraph, decision_id: str, span: Span) -> Path:
    """BFS for the shortest path from the span's entry to any state offering `decision_id`."""
    targets = set(graph.states_offering(decision_id))
    if not targets:
        return []
    queue = deque([(span.entry_state, [])])
    visited = {span.entry_state}
    while queue:
        state_id, prefix = queue.popleft()
        if state_id in targets:
            return prefix
        state = graph.state(state_id)
        if state is None or len(prefix) >= _longest(graph):
            continue
        # An exit is where the block hands on, so the search stops there for the same reason the
        # walk does: a route that leaves the block is not a route through it.
        if state.is_terminal or (prefix and state_id in span.exit_states):
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
                               fired: Dict[str, int], taken: int,
                               span: Span) -> Optional[Path]:
    """BFS onward from `state_id` to the end of the span, or ``None`` if it cannot be reached.

    The end of the span is either an exit it declares or a state the intake marks as ending the
    interaction, exactly as in :func:`walk_paths` -- the two have to agree about where a route
    finishes, or an augmented route would run past the block it belongs to.

    Attempt counts are carried in rather than restarted, because they are what makes the rest of
    the route legal: a suffix that fires a decision a fourth time is not a route the agent has.
    """
    def finished(candidate: str) -> bool:
        state = graph.state(candidate)
        return candidate in span.exit_states or (state is not None and state.is_terminal)

    if finished(state_id):
        return []

    queue = deque([(state_id, [], fired)])
    seen = {(state_id, tuple(sorted(fired.items())))}
    while queue:
        current, suffix, used = queue.popleft()
        state = graph.state(current)
        if state is None or taken + len(suffix) >= _longest(graph):
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
                if finished(next_state):
                    return suffix + [step]
                after = {**used, decision_id: occurrence + 1}
                mark = (next_state, tuple(sorted(after.items())))
                if landed is None or mark in seen:
                    continue
                seen.add(mark)
                queue.append((next_state, suffix + [step], after))
    return None


def augment_variants(graph: DecisionGraph, paths: List[Path],
                     span: Optional[Span] = None) -> List[Path]:
    """A focused route for every declared (decision, variant) the DFS missed.

    Carried on to an ending rather than stopped at the outcome being reached for. These exist to
    exercise an outcome the exhaustive walk could not get to -- usually one behind a retry limit --
    and stopping the moment it fires made every one of them a route with no declared ending, which
    is the one thing a scenario cannot be. An outcome whose continuation dead-ends is dropped: it
    is unreachable in a complete route, so there is no conversation to ask anybody to run.
    """
    if span is None:
        endings = frozenset(s.id for s in graph.states.values() if s.is_terminal)
        span = Span("", "", graph.start_states[0] if graph.start_states else "", endings)

    already = covered_variants(paths)
    within = _decisions_within(graph, span)
    extra: List[Path] = []
    for decision in graph.decisions.values():
        if decision.out_of_scope or not _belongs_to(graph, decision, span, within):
            continue
        missing = [v for v in decision.variants if (decision.id, v) not in already]
        if not missing:
            continue
        # One search per decision, not per outcome: the route *to* a decision is the same
        # whichever of its outcomes is being reached for.
        prefix = _shortest_prefix_to(graph, decision.id, span)
        fired: Dict[str, int] = {}
        for step in prefix:
            fired[step.decision_id] = fired.get(step.decision_id, 0) + 1
        for variant in missing:
            occurrence = fired.get(decision.id, 0)
            landing = graph.successor(decision.id, variant, occurrence)
            reached = prefix + [Step(decision.id, variant, landing)]
            suffix = _shortest_suffix_to_ending(
                graph, landing, {**fired, decision.id: occurrence + 1}, len(reached), span)
            if suffix is None:
                continue
            extra.append(reached + suffix)
            already.add((decision.id, variant))
    return extra


def enumerate_paths(graph: DecisionGraph, span: Optional[Span] = None,
                    limits: Optional[Limits] = None) -> Tuple[List[Path], List[Path]]:
    """Return (walked, augmented) paths over one span, de-duplicated by decision-variant signature."""
    walked = walk_paths(graph, span, limits)
    seen: Set[tuple] = set()

    def keep(path: Path) -> bool:
        signature = tuple((s.decision_id, s.variant) for s in path)
        if not signature or signature in seen:
            return False
        seen.add(signature)
        return True

    unique_walked = [p for p in walked if keep(p)]
    unique_augmented = [p for p in augment_variants(graph, walked, span) if keep(p)]
    return unique_walked, unique_augmented


def enumerate_by_span(graph: DecisionGraph, capabilities: Sequence[Capability],
                      limits: Optional[Limits] = None) -> List[Tuple[Span, List[Path],
                                                                     List[Path]]]:
    """Every span, with the routes through it. The whole enumeration, in one call.

    De-duplication is per span rather than across the set. Two capabilities sharing a decision
    would otherwise have the second one silently lose the routes the first had already claimed --
    and the point of walking blocks separately is that each block is tested on its own terms.
    """
    return [(span,) + enumerate_paths(graph, span, limits)
            for span in spans_for(graph, capabilities)]


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
