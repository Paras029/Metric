"""Drawing the declared decision graph as an SVG."""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from metric.domain.graph import DecisionGraph, endings_not_reached, entry_candidates, exits_for
from metric.shared.text import parse_reached_via
from metric.domain.models import ORIGIN_PROPOSED, Decision, IntakeData, State

BOX_WIDTH = 190
BOX_HEIGHT = 62
GAP_X = 52
GAP_Y = 116
MARGIN = 28
MAX_LABEL = 22

# A retry loop -- a decision whose own outcome leads back to itself or to an earlier point in the
# flow -- cannot be drawn as a normal top-to-bottom arrow without it cutting straight across every
# row in between. Those edges are routed instead through a side lane: out the right of the source,
# down (or up) a dedicated vertical lane past the edge of the ordinary flow, and back in the right
# of the target. Each such edge gets its own lane so two loops never run on top of each other.
LOOP_LANE_GAP = 30

# How far apart the labels of two edges leaving the same box are pushed. See _label_position.
LABEL_STAGGER = 19
LABEL_HEIGHT = 17
LOOP_MARGIN = 34

START = "start"
DECISION = "decision"
TERMINAL = "terminal"
ORPHAN = "orphan"
BLOCK = "block"
"""One capability, drawn collapsed: the whole of its internal branching as a single box."""

_START_ID = "__start__"


@dataclass
class Node:
    """One box: where the interaction opens, a point where it branches, or a way it ends."""

    id: str
    kind: str
    title: str
    caption: str
    detail: str
    pending: bool = False
    outcome_type: str = ""
    out_of_scope: bool = False
    depth: int = 0
    x: float = 0.0
    y: float = 0.0

    merged_ids: Tuple[str, ...] = ()
    """Every state id this box stands for, where several declare the same ending.

    A graph drawn from a real intake is full of endings declared once per route into them --
    "Handed to a case handler" as S-07, S-11 and S-19, because each was written as the destination
    of one decision outcome and nothing joined them up. Drawn as three boxes that is three
    different endings, which is not what the agent does and not what anyone reading the picture
    should conclude. Drawn as one box with three arrows into it, it is the flow as it actually
    runs, and the ids it stands for are named in the hover so nothing is hidden.
    """


@dataclass
class Edge:
    """One arrow: the state the interaction reaches, and the outcome that took it there."""

    source: str
    target: str
    outcome: str
    state_label: str
    detail: str
    pending: bool = False

    state_id: str = ""
    """Which declared state this arrow *is*. An intermediate state has no box -- it is the arrow
    between the decision that produced it and the decision it offers -- so this is the only way to
    point at one from outside the drawing, which is what the editor does when a state is selected
    in a list beside it."""

    out_of_scope: bool = False
    """Whether this edge leaves a decision marked out of scope -- see :class:`Node`. Drawn muted
    rather than left off: the route is declared, it is just not one the scenario space walks."""

    is_back: bool = False
    """Whether this edge points back to the same depth or an earlier one -- a retry loop rather
    than a step forward. Set once depths are known, and what decides whether it is drawn as an
    ordinary arrow or routed through a side lane."""

    lane_x: float = 0.0
    """Where a back edge's side lane sits. Unused for an ordinary forward edge."""


@dataclass
class Layout:
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    unreachable: List[str] = field(default_factory=list)
    orphans: List[str] = field(default_factory=list)

    duplicate_endings: List[Tuple[str, ...]] = field(default_factory=list)
    """Groups of state ids that declare the same ending. Drawn as one box each -- see
    :attr:`Node.merged_ids` -- and reported as a finding, because an intake that says the same
    thing three times is a declaration somebody should tidy rather than a fact about the agent."""


class _Wiring:
    """The declared graph, wired by the same code that walks it."""

    def __init__(self, intake: IntakeData) -> None:
        self._graph = DecisionGraph(intake.decisions, intake.states)
        self._by_id = {state.id: state for state in intake.states}

    @property
    def starts(self) -> List[State]:
        found = [self._by_id[i] for i in self._graph.start_states if i in self._by_id]
        return found or ([next(iter(self._by_id.values()))] if self._by_id else [])

    def target_of(self, decision_id: str, variant: str) -> Optional[State]:
        """The state a decision outcome leads to, or ``None`` where nothing declares one."""
        return self._by_id.get(self._graph.successor(decision_id, variant))


def _panel(heading: str, rows: Sequence[Tuple[str, str]], footer: str = "") -> str:
    """Hover text as a small labelled panel rather than one run-on line."""
    lines = [heading]
    body = [f"{label}: {value}" for label, value in rows if str(value).strip()]
    if body:
        lines += [""] + body
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def _ends_note(state: State) -> str:
    """How an interaction finishes here, where it does."""
    if not state.is_terminal:
        return ""
    return (f"Ends the interaction ({state.outcome_type})" if state.outcome_type
            else "Ends the interaction")


def _decision_detail(decision: Decision) -> str:
    return _panel(
        f"{decision.id} · {decision.name}",
        [("Outcomes", " / ".join(decision.variants) or "none declared"),
         ("Capability", decision.trigger_capability),
         ("Decided from", decision.inputs),
         ("Input source", decision.input_source),
         ("Attempts allowed", str(decision.max_attempts) if decision.max_attempts > 1 else ""),
         ("Selected when", decision.outcome_condition)],
        footer=("Out of scope — declared but not walked; no scenario is generated through it."
                if decision.out_of_scope else ""))


def _state_detail(state: State, also: Sequence[State] = ()) -> str:
    reached = ", ".join([state.reached_via] + [s.reached_via for s in also if s.reached_via])
    footer = _ends_note(state)
    if also:
        footer = (f"{footer}\n\nDeclared {len(also) + 1} times over, as "
                  f"{', '.join([state.id] + [s.id for s in also])}. Drawn once, because they "
                  f"describe the same ending.").strip()
    return _panel(
        f"{state.id} · {state.description or 'no description'}",
        [("Reached via", reached)],
        footer=footer)


def _ending_key(state: State) -> Optional[tuple]:
    """What makes two declared endings the same ending, or ``None`` for one that cannot be judged."""
    words = " ".join((state.description or "").split()).strip().rstrip(".").lower()
    return (words, (state.outcome_type or "").strip().lower()) if words else None


def _edge_detail(decision: Decision, variant: str, state: State) -> str:
    """One arrow: the outcome taken, and the position it leads to."""
    return _panel(
        f"{decision.id} · {decision.name} → {variant}",
        [("Leads to", f"{state.id} · {state.description}" if state.description else state.id)],
        footer=_ends_note(state))


def build_layout(intake: IntakeData, pending_decisions: Sequence[str] = (),
                 pending_states: Sequence[str] = ()) -> Layout:
    """Place every decision by its distance from the start, and connect them by the states between."""
    layout = Layout()
    if not intake.decisions and not intake.states:
        return layout

    pending_decisions, pending_states = set(pending_decisions), set(pending_states)
    wiring = _Wiring(intake)
    starts = wiring.starts

    layout.nodes[_START_ID] = Node(
        id=_START_ID, kind=START, title="Start",
        caption=(starts[0].description if starts else "The interaction opens"),
        detail="Where the interaction opens" + (f" ({starts[0].id})" if starts else ""))

    for decision in intake.decisions:
        layout.nodes[decision.id] = Node(
            id=decision.id, kind=DECISION, title=decision.id, caption=decision.name,
            detail=_decision_detail(decision), pending=decision.id in pending_decisions,
            out_of_scope=decision.out_of_scope)

    # One box per *ending*, not one per declared state. See Node.merged_ids for why.
    endings: Dict[tuple, List[State]] = {}
    for state in intake.states:
        if not state.is_terminal:
            continue
        key = _ending_key(state)
        endings.setdefault(key or ("", state.id), []).append(state)

    stands_for: Dict[str, str] = {}
    for group in endings.values():
        head, also = group[0], group[1:]
        for state in group:
            stands_for[state.id] = head.id
        layout.nodes[head.id] = Node(
            id=head.id, kind=TERMINAL, title=head.id, caption=head.description,
            detail=_state_detail(head, also),
            pending=any(s.id in pending_states for s in group),
            outcome_type=head.outcome_type,
            merged_ids=tuple(s.id for s in group))
    layout.duplicate_endings = sorted(
        tuple(s.id for s in group) for group in endings.values() if len(group) > 1)

    def _leaving(state: State, source: str, pending: bool) -> None:
        """Draw the arrow out of a state into each decision it leads to."""
        for decision_id in state.next_decisions:
            if decision_id not in layout.nodes:
                continue
            layout.edges.append(Edge(
                source=source, target=decision_id, outcome="",
                state_label=state.description or state.id, state_id=state.id,
                detail=_state_detail(state),
                pending=pending or state.id in pending_states))

    for state in starts:
        _leaving(state, _START_ID, False)

    for decision in intake.decisions:
        for variant in decision.variants:
            state = wiring.target_of(decision.id, variant)
            if state is None:
                continue
            pending = decision.id in pending_decisions or state.id in pending_states
            if state.is_terminal:
                layout.edges.append(Edge(
                    source=decision.id, target=stands_for.get(state.id, state.id), outcome=variant,
                    state_label=state.description or state.id, state_id=state.id,
                    detail=_edge_detail(decision, variant, state),
                    pending=pending, out_of_scope=decision.out_of_scope))
                continue
            for decision_id in state.next_decisions:
                if decision_id not in layout.nodes:
                    continue
                layout.edges.append(Edge(
                    source=decision.id, target=decision_id, outcome=variant,
                    state_label=state.description or state.id, state_id=state.id,
                    detail=_edge_detail(decision, variant, state),
                    pending=pending, out_of_scope=decision.out_of_scope))

    _assign_depths(layout)

    # A retry loop is any edge that does not move forward: the BFS above gave every node the
    # depth of the *shortest* way to reach it, so an edge landing at that node's own depth or
    # shallower is, by construction, not the path that produced that depth -- it is a loop back.
    for edge in layout.edges:
        source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
        if source and target:
            edge.is_back = target.depth <= source.depth

    # A state nobody can reach is a defect in the declaration, not something to hide.
    reachable = {e.target for e in layout.edges} | {_START_ID}
    layout.unreachable = sorted(
        s.id for s in intake.states
        if s.is_terminal and stands_for.get(s.id, s.id) not in reachable
        and stands_for.get(s.id, s.id) in layout.nodes)
    layout.orphans = sorted(
        node.id for node in layout.nodes.values()
        if node.id not in reachable and node.kind in (DECISION, TERMINAL))
    for node_id in layout.orphans:
        layout.nodes[node_id].kind = ORPHAN if layout.nodes[node_id].pending else \
            layout.nodes[node_id].kind

    _place(layout)
    return layout


def _leaves_at(graph, capability, decisions: Sequence[Decision]) -> List[str]:
    """Every state a route through this capability can leave it at."""
    from metric.domain.graph import Span, _decisions_within

    if not capability.entry_states:
        return []

    # What the block owns, by the tag on each decision -- the same basis the drawing uses to
    # decide which box belongs to which block, so the two cannot disagree. Reachability is *not*
    # the basis: a walk from verification's entry that follows a route back into identification
    # reaches identification's decisions too, and counting those as verification's would make the
    # return look like ordinary internal branching and hide the arrow this exists to draw.
    reachable = _decisions_within(graph, Span(
        capability.id, capability.name or capability.id,
        capability.entry_states[0], frozenset(capability.exit_states)))
    tagged = {d.id for d in decisions if d.trigger_capability}
    mine = {d for d in reachable if d not in tagged}
    mine |= {d.id for d in decisions if d.trigger_capability == capability.id}

    inside = set(capability.entry_states)
    for decision_id in mine:
        inside.update(graph.states_offering(decision_id))

    found = [s for s in capability.exit_states]
    for decision_id in sorted(mine):
        decision = graph.decision(decision_id)
        if decision is None:
            continue
        for variant in decision.variants:
            landing = graph.state(graph.successor(decision_id, variant))
            if landing is None or landing.id in found:
                continue
            if landing.is_terminal or landing.id not in inside:
                found.append(landing.id)
    return found


def build_block_layout(intake: IntakeData) -> Layout:
    """The agent as its capabilities, with the internal branching of each one folded away."""
    layout = Layout()
    bounded = [c for c in intake.capabilities if c.is_bounded]
    if not bounded:
        return layout

    graph = DecisionGraph(intake.decisions, intake.states)
    described = {s.id: s for s in intake.states}
    entered_by: Dict[str, str] = {}
    for capability in bounded:
        for state_id in capability.entry_states:
            entered_by.setdefault(state_id, capability.id)

    # Which block a state belongs to, for a route that re-enters one part way through rather than
    # at its declared entry. Verification deciding the cardmember must be identified again does not
    # politely route to identification's entry state -- it routes to whichever state asks the
    # question, which is somewhere in the middle. Entry states win, because those are what somebody
    # declared; the rest is worked out from which capability's decisions each state offers.
    owner_of: Dict[str, str] = dict(entered_by)
    for capability in bounded:
        for decision in intake.decisions:
            if decision.trigger_capability != capability.id:
                continue
            for state_id in graph.states_offering(decision.id):
                owner_of.setdefault(state_id, capability.id)

    starts = [s.id for s in intake.states if not parse_reached_via(s.reached_via)]
    opens = next((entered_by[s] for s in starts if s in entered_by), None)

    layout.nodes[_START_ID] = Node(
        id=_START_ID, kind=START, title="Start",
        caption=described[starts[0]].description if starts else "The interaction opens",
        detail="Where the interaction opens")

    for capability in bounded:
        inside = [d.id for d in intake.decisions if d.trigger_capability == capability.id]
        layout.nodes[capability.id] = Node(
            id=capability.id, kind=BLOCK, title=capability.id,
            caption=capability.name or capability.id,
            detail=_panel(capability.name or capability.id, [
                ("Type", capability.type or "not declared"),
                ("Entered at", ", ".join(capability.entry_states)),
                ("Hands on or ends at", ", ".join(capability.exit_states)),
                ("Decisions inside", ", ".join(inside) or "none tagged with it"),
            ], footer="Click to open this block in the full graph"),
            merged_ids=tuple(inside))

    if opens:
        layout.edges.append(Edge(source=_START_ID, target=opens, outcome="",
                                 state_label=layout.nodes[_START_ID].caption,
                                 detail="Where the interaction opens"))

    # One arrow per pair of blocks, however many positions they hand over at. Two blocks joined
    # at two positions is two edges between the same pair of boxes, drawn on top of each other
    # with their labels colliding -- and the fact worth reading is that they join, with how many
    # ways as a detail on the arrow rather than as a second arrow underneath the first.
    handoffs: Dict[Tuple[str, str], List[State]] = {}
    returns: Dict[Tuple[str, str], List[State]] = {}
    for capability in bounded:
        # Every way out of the block, not only the ones somebody listed as exits. See
        # :func:`_leaves_at`: the declared hand-offs, the endings a decision inside can reach, and
        # the routes back into another block that make the picture something other than a chain.
        for exit_id in _leaves_at(graph, capability, intake.decisions):
            state = described.get(exit_id)
            if state is None:
                continue
            onward = owner_of.get(exit_id)
            if onward and onward != capability.id:
                # A declared boundary -- this block's exit is that block's entry -- is the ordinary
                # forward hand-off. Anything else is a route back into a block, usually part way
                # through it: verification deciding the cardmember has to be identified again lands
                # on whichever state asks the question, not on identification's entry. Kept apart
                # because they read differently and are drawn differently, and because a picture
                # that calls a return a hand-off says the agent runs in a straight line.
                declared = exit_id in capability.exit_states and exit_id in entered_by
                where = handoffs if declared else returns
                where.setdefault((capability.id, onward), []).append(state)
            elif state.is_terminal:
                # Its own box rather than a line on the block, because how a block can end is the
                # first thing lost when one is summarised, and it is what the pack tests.
                ending = f"{capability.id}:{exit_id}"
                layout.nodes[ending] = Node(
                    id=ending, kind=TERMINAL, title=exit_id, caption=state.description,
                    detail=_state_detail(state), outcome_type=state.outcome_type,
                    merged_ids=(exit_id,))
                layout.edges.append(Edge(
                    source=capability.id, target=ending, outcome="ends",
                    state_label=state.description or exit_id, detail=_state_detail(state)))

    for kind, joins in (("hands on", handoffs), ("returns to", returns)):
        for (source, target), positions in joins.items():
            label = (positions[0].description or positions[0].id if len(positions) == 1
                     else f"{len(positions)} ways")
            named = layout.nodes[source].caption
            verb = "hands on to" if kind == "hands on" else "routes back into"
            layout.edges.append(Edge(
                source=source, target=target, outcome=kind, state_label=label,
                detail=f"{named} {verb} {layout.nodes[target].caption} at "
                       + "; ".join(f"{p.id} ({p.description})" for p in positions)))

    _assign_depths(layout)
    for edge in layout.edges:
        source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
        if source and target:
            edge.is_back = target.depth <= source.depth
    _place(layout)
    return layout


def _assign_depths(layout: Layout) -> None:
    """Breadth-first from the start, so depth reads as how far into the interaction a step is."""
    outgoing: Dict[str, List[str]] = {}
    for edge in layout.edges:
        outgoing.setdefault(edge.source, []).append(edge.target)

    frontier, seen = [(_START_ID, 0)], {_START_ID}
    while frontier:
        node_id, depth = frontier.pop(0)
        node = layout.nodes.get(node_id)
        if node is not None:
            node.depth = depth
        for target in outgoing.get(node_id, []):
            if target not in seen:
                seen.add(target)
                frontier.append((target, depth + 1))

    # What the walk never reached. It is parked below everything reachable rather than dropped --
    # a block nothing routes into is a defect in the declaration and belongs on the page -- but
    # parking every one of them on a single row is what made the drawing unreadable in exactly the
    # case it exists to expose: an unreachable capability, its endings, and whatever it hands on
    # to all landed side by side on one line, with arrows running horizontally between boxes in
    # the same row and every label stacked on top of the next.
    parked = max((n.depth for n in layout.nodes.values() if n.id in seen), default=0) + 1
    stranded = [node_id for node_id in layout.nodes if node_id not in seen]
    reached_within = {edge.target for edge in layout.edges
                      if edge.source in set(stranded) and edge.target in set(stranded)}
    roots = [node_id for node_id in stranded if node_id not in reached_within] or stranded

    frontier = [(node_id, parked) for node_id in roots]
    seen.update(roots)
    while frontier:
        node_id, depth = frontier.pop(0)
        node = layout.nodes.get(node_id)
        if node is not None:
            node.depth = depth
        for target in outgoing.get(node_id, []):
            if target not in seen:
                seen.add(target)
                frontier.append((target, depth + 1))

    # Anything a cycle among the stranded nodes hid from even that walk.
    for node in layout.nodes.values():
        if node.id not in seen:
            node.depth = parked


def _place(layout: Layout) -> None:
    rows: Dict[int, List[Node]] = {}
    for node in layout.nodes.values():
        rows.setdefault(node.depth, []).append(node)

    widest = max((len(row) for row in rows.values()), default=1)
    content_width = MARGIN * 2 + widest * BOX_WIDTH + (widest - 1) * GAP_X

    # Across before down. The stagger needs to know how wide each label is and where it sits
    # horizontally; nothing about it depends on y, and computing it first is what lets each row's
    # gap be sized to the labels that actually cross it.
    for depth, row in sorted(rows.items()):
        row.sort(key=lambda n: (n.kind == TERMINAL, n.id))
        span = len(row) * BOX_WIDTH + (len(row) - 1) * GAP_X
        left = (content_width - span) / 2
        for column, node in enumerate(row):
            node.x = left + column * (BOX_WIDTH + GAP_X)
            node.depth = depth

    # How many rows of labels have to fit between each pair of rows of boxes. A fixed gap fits
    # three; a decision with five outcomes needs five, and the fourth and fifth were being drawn
    # underneath the boxes below -- a label a reader can see is behind a box is worse than one
    # that is not there, because it is still legible enough to be read as belonging to it.
    rank_of = _stagger(layout)
    stacked: Dict[int, int] = {}
    for edge in layout.edges:
        source = layout.nodes.get(edge.source)
        if source is None or id(edge) not in rank_of:
            continue
        stacked[source.depth] = max(stacked.get(source.depth, 1), rank_of[id(edge)] + 1)

    # A label at rank r sits at the gap's midpoint plus r stagger steps, so the lowest one clears
    # the row below only where gap/2 + r*LABEL_STAGGER + LABEL_HEIGHT/2 <= gap -- which works out
    # as twice the stagger per rank, plus the label's own height. A fixed gap of GAP_Y fits three
    # rows of labels; a decision with five outcomes needs five, and the fourth and fifth used to
    # be drawn underneath the boxes below. A label a reader can see is behind a box is worse than
    # one that is not there, because it is still legible enough to be read as belonging to it.
    top = float(MARGIN)
    bottom = top
    for depth, row in sorted(rows.items()):
        for node in row:
            node.y = top
        bottom = top + BOX_HEIGHT
        needed = (stacked.get(depth, 1) - 1) * LABEL_STAGGER * 2 + LABEL_HEIGHT + 8
        top += BOX_HEIGHT + max(GAP_Y, needed)
    layout.height = bottom + MARGIN

    # Back edges get their own lane, in a strip appended to the right of the ordinary flow --
    # never inside it, so a loop can never run across a row it does not belong to. Longer loops
    # (more rows spanned) are given the outer lanes, so a short loop nested inside a long one
    # still reads as nested rather than crossing it.
    back_edges = sorted((e for e in layout.edges if e.is_back),
                        key=lambda e: abs(layout.nodes[e.target].depth - layout.nodes[e.source].depth))
    for index, edge in enumerate(back_edges):
        edge.lane_x = content_width + LOOP_MARGIN + index * LOOP_LANE_GAP

    layout.width = content_width + (LOOP_MARGIN + len(back_edges) * LOOP_LANE_GAP
                                    if back_edges else 0)


def _wrap(text: str, limit: int = MAX_LABEL, lines: int = 2) -> List[str]:
    """Break a label onto at most two lines, ending in an ellipsis rather than overflowing."""
    words, out, current = str(text or "").split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        out.append(current)
        current = word
        if len(out) == lines:
            break
    if current and len(out) < lines:
        out.append(current)
    if not out:
        return [""]
    if len(" ".join(words)) > sum(len(line) for line in out):
        out[-1] = out[-1][:limit - 1].rstrip() + "…"
    return out


def _edge_path(edge: Edge, source: Node, target: Node) -> str:
    """The arrow's path: a plain top-to-bottom curve for a forward step, or a routed side lane
    for a retry loop -- see :attr:`Edge.is_back`. A loop drawn the ordinary way would run bottom
    to top straight through whatever rows sit between the two, which is exactly the overlap this
    exists to avoid: it leaves the flow on the right of the source, travels its own lane past the
    edge of the ordinary rows, and re-enters the target from the right as well."""
    if edge.is_back:
        x1, y1 = source.x + BOX_WIDTH, source.y + BOX_HEIGHT / 2
        x2, y2 = target.x + BOX_WIDTH, target.y + BOX_HEIGHT / 2
        return (f"M {x1:.1f} {y1:.1f} L {edge.lane_x:.1f} {y1:.1f} "
                f"L {edge.lane_x:.1f} {y2:.1f} L {x2:.1f} {y2:.1f}")

    x1, y1 = source.x + BOX_WIDTH / 2, source.y + BOX_HEIGHT
    x2, y2 = target.x + BOX_WIDTH / 2, target.y
    if abs(x1 - x2) < 1:
        return f"M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}"
    midpoint = (y1 + y2) / 2
    return (f"M {x1:.1f} {y1:.1f} C {x1:.1f} {midpoint:.1f} "
            f"{x2:.1f} {midpoint:.1f} {x2:.1f} {y2:.1f}")


def _label_width(edge: Edge) -> float:
    """How wide an edge's label box is. The same arithmetic the rect is drawn with, because a
    stagger computed from a different width is a stagger that does not match the picture."""
    return len(_edge_label(edge)) * 5.6 + 14


def _stagger(layout: Layout) -> Dict[int, int]:
    """Which row each edge's label sits on, so no two labels are drawn over each other."""
    # Banded on the pair of rows an edge spans rather than on where its label lands, so this can
    # be worked out before any y is assigned -- which is what lets :func:`_place` give a row enough
    # vertical room for the labels crossing it instead of letting them run into the boxes below.
    bands: Dict[Tuple[int, int], List[Edge]] = {}
    for edge in layout.edges:
        source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
        if edge.is_back or not source or not target:
            continue
        bands.setdefault((source.depth, target.depth), []).append(edge)

    rank_of: Dict[int, int] = {}
    for band in bands.values():
        # Left to right, so the stagger reads as a fan rather than as an arbitrary shuffle.
        band.sort(key=lambda e: (layout.nodes[e.source].x + layout.nodes[e.target].x) / 2)
        rows: List[List[tuple]] = []
        for edge in band:
            source, target = layout.nodes[edge.source], layout.nodes[edge.target]
            middle = (source.x + target.x) / 2 + BOX_WIDTH / 2
            half = _label_width(edge) / 2
            span = (middle - half, middle + half)
            for index, taken in enumerate(rows):
                if all(span[1] <= left or span[0] >= right for left, right in taken):
                    taken.append(span)
                    rank_of[id(edge)] = index
                    break
            else:
                rows.append([span])
                rank_of[id(edge)] = len(rows) - 1
    return rank_of


def _label_position(edge: Edge, source: Node, target: Node, rank: int = 0) -> tuple:
    """Where an edge's outcome label sits -- along the lane for a loop, at the midpoint otherwise."""
    if edge.is_back:
        y1, y2 = source.y + BOX_HEIGHT / 2, target.y + BOX_HEIGHT / 2
        return edge.lane_x, (y1 + y2) / 2
    mid_x = (source.x + target.x) / 2 + BOX_WIDTH / 2
    mid_y = (source.y + BOX_HEIGHT + target.y) / 2 + rank * LABEL_STAGGER
    return mid_x, mid_y


def _corner(kind: str) -> float:
    """How round a box is, which is what says what kind of thing it stands for."""
    if kind in (START, TERMINAL, ORPHAN):
        return BOX_HEIGHT / 2
    return 6 if kind == BLOCK else 3


def _inside_note(node: Node) -> str:
    """How much a block is standing in for, printed under its name."""
    if node.kind != BLOCK or not node.merged_ids:
        return ""
    held = len(node.merged_ids)
    return (f'<text class="graph__inside" x="{node.x + BOX_WIDTH / 2:.1f}" '
            f'y="{node.y + BOX_HEIGHT - 7:.1f}">'
            f'{held} decision{"" if held == 1 else "s"}</text>')


def _edge_label(edge: Edge) -> str:
    """What the arrow says: the state it leads to, and the outcome that took it there."""
    state = edge.state_label if len(edge.state_label) <= 26 else edge.state_label[:25] + "…"
    return f"{edge.outcome} → {state}" if edge.outcome else state


def _block_of(intake: IntakeData) -> Dict[str, str]:
    """Which capability each box in the detailed graph belongs to."""
    owner = {d.id: d.trigger_capability for d in intake.decisions if d.trigger_capability}
    for capability in intake.capabilities:
        if not capability.is_bounded:
            continue
        for state_id in tuple(capability.entry_states) + tuple(capability.exit_states):
            owner.setdefault(state_id, capability.id)
    return owner


def render_svg(intake: IntakeData, pending_decisions: Sequence[str] = (),
               pending_states: Sequence[str] = ()) -> str:
    """The declared graph as a self-contained SVG. Returns an empty string for an empty intake."""
    layout = build_layout(intake, pending_decisions, pending_states)
    return _render(layout, "The declared decision graph", _block_of(intake))


def render_blocks_svg(intake: IntakeData) -> str:
    """The same agent with each capability collapsed to one box. Empty where no spans are drawn."""
    return _render(build_block_layout(intake), "The agent's capabilities", {})


def _render(layout: Layout, aria_label: str, block_of: Dict[str, str]) -> str:
    """One layout as an SVG. Shared by the detailed graph and the collapsed one, because two
    copies of the drawing code is how the two quietly stop looking like the same picture."""
    if not layout.nodes:
        return ""

    parts = [
        f'<svg class="graph" viewBox="0 0 {layout.width:.0f} {layout.height:.0f}" '
        f'width="{layout.width:.0f}" height="{layout.height:.0f}" '
        f'role="img" aria-label="{html.escape(aria_label)}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        '<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 8 4 L 0 8 z" class="graph__arrow"/></marker></defs>',
    ]

    for edge in layout.edges:
        source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
        if not source or not target:
            continue
        classes = "graph__edge" + (" graph__edge--pending" if edge.pending else "") + \
            (" graph__edge--loop" if edge.is_back else "") + \
            (" graph__edge--out-of-scope" if edge.out_of_scope else "")
        # Two paths on the same line: the arrow, and a wide soft one behind it that carries how
        # much of the scenario space flows along it. Drawn always and invisible until something
        # paints it, because adding an element from script would mean the standalone page and the
        # interface producing different SVG from one function.
        drawn = _edge_path(edge, source, target)
        parts.append(
            f'<g class="{classes}" data-source="{html.escape(edge.source)}" '
            f'data-target="{html.escape(edge.target)}" '
            f'data-outcome="{html.escape(edge.outcome)}">'
            f'<title>{html.escape(edge.detail)}</title>'
            f'<path class="graph__flow" d="{drawn}"/>'
            f'<path d="{drawn}" marker-end="url(#arrow)"/></g>')

    # Labels are drawn after every edge so no path crosses over the text. A loop's label is
    # rotated to run along its lane -- upright, it would need the label's full text width just to
    # fit between two lanes 30px apart, which no reasonable width leaves room for.
    rank_of = _stagger(layout)

    for edge in layout.edges:
        source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
        if not source or not target:
            continue
        label = _edge_label(edge)
        mid_x, mid_y = _label_position(edge, source, target, rank_of.get(id(edge), 0))
        width = _label_width(edge)
        classes = "graph__label" + (" graph__label--loop" if edge.is_back else "")
        rotate = f' transform="rotate(-90 {mid_x:.1f} {mid_y:.1f})"' if edge.is_back else ""
        parts.append(
            f'<g class="{classes}"{rotate} data-source="{html.escape(edge.source)}" '
            f'data-target="{html.escape(edge.target)}" '
            f'data-outcome="{html.escape(edge.outcome)}">'
            f'<title>{html.escape(edge.detail)}</title>'
            f'<rect x="{mid_x - width / 2:.1f}" y="{mid_y - 9:.1f}" width="{width:.1f}" '
            f'height="17" rx="8.5"/>'
            f'<text x="{mid_x:.1f}" y="{mid_y + 3.5:.1f}" text-anchor="middle">'
            f'{html.escape(label)}</text></g>')

    for node in layout.nodes.values():
        classes = ["graph__node", f"graph__node--{node.kind}"]
        if node.outcome_type:
            classes.append(f"graph__node--{node.outcome_type.split()[0].lower()}")
        if node.pending:
            classes.append("graph__node--pending")
        if node.out_of_scope:
            classes.append("graph__node--out-of-scope")
        detail = node.detail
        if node.id in layout.unreachable:
            classes.append("graph__node--unreachable")
            detail += "\n\nNot reachable from the start."
        elif node.id in layout.orphans:
            classes.append("graph__node--unreachable")
            detail += "\n\nNot connected to anything yet."

        owner = block_of.get(node.id, "")
        block = f' data-capability="{html.escape(owner)}"' if owner else ""
        if node.kind == BLOCK:
            block = f' data-capability="{html.escape(node.id)}"'

        lines = _wrap(node.caption or node.title)
        text_y = node.y + (BOX_HEIGHT / 2) - (len(lines) - 1) * 6 + 1
        spans = "".join(
            f'<tspan x="{node.x + BOX_WIDTH / 2:.1f}" dy="{0 if i == 0 else 12}">'
            f'{html.escape(line)}</tspan>' for i, line in enumerate(lines))

        # A block gets a second outline inset inside the first. Two rects rather than a border
        # style, because SVG has no such thing and because the doubled edge is the statechart
        # convention for a composite state -- which is exactly what a capability is here.
        inner = ""
        if node.kind == BLOCK:
            inner = (f'<rect class="graph__inner" x="{node.x + 4:.1f}" y="{node.y + 4:.1f}" '
                     f'width="{BOX_WIDTH - 8}" height="{BOX_HEIGHT - 8}" '
                     f'rx="{max(_corner(node.kind) - 2, 0):.1f}"/>')

        parts.append(
            f'<g class="{" ".join(classes)}" data-node="{html.escape(node.id)}"{block}>'
            f'<title>{html.escape(detail)}</title>'
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{BOX_WIDTH}" height="{BOX_HEIGHT}" '
            f'rx="{_corner(node.kind)}"/>{inner}'
            f'<text class="graph__id" x="{node.x + 10:.1f}" y="{node.y + 15:.1f}">'
            f'{html.escape(node.title)}</text>'
            f'<text class="graph__caption" y="{text_y:.1f}">{spans}</text>'
            f'{_inside_note(node)}</g>')

    parts.append("</svg>")
    return "".join(parts)


def graph_summary(intake: IntakeData, pending_decisions: Sequence[str] = (),
                  pending_states: Sequence[str] = ()) -> Dict[str, object]:
    """Counts worth stating beside the picture, including anything the declaration got wrong."""
    layout = build_layout(intake, pending_decisions, pending_states)
    terminal = [s for s in intake.states if s.is_terminal]
    graph = DecisionGraph(intake.decisions, intake.states)
    states = {s.id: s for s in intake.states}
    return {
        "states": len(intake.states),
        "terminal": len(terminal),
        "decisions": len(intake.decisions),
        "capabilities": len(intake.capabilities),
        "personas": len(intake.personas),
        "tools": len(intake.tools),
        "outcomes": sum(len(d.variants) for d in intake.decisions),
        "out_of_scope": sum(1 for d in intake.decisions if d.out_of_scope),
        "unreachable": layout.unreachable,
        "orphans": layout.orphans,
        "duplicate_endings": layout.duplicate_endings,
        "depth": max((n.depth for n in layout.nodes.values()), default=0) + 1,
        # Endings the enumeration cannot arrive at, with the reason. Shown beside the drawing
        # rather than only after a run, because the fix for every one of them is an edit to the
        # declaration and the declaration is on this page.
        "unreached_endings": [
            {"capability": u.capability_id,
             "state": u.state_id,
             "label": (states[u.state_id].description or u.state_id)
                      if u.state_id in states else u.state_id,
             "reason": u.reason}
            for u in endings_not_reached(graph, intake.capabilities, intake.decisions)],
    }


def routes(intake: IntakeData, scenarios: Sequence) -> Dict[str, dict]:
    """Where each scenario runs in the drawing: the boxes it passes through and the arrows it takes."""
    layout = build_layout(intake)
    # Endings declared several times are drawn as one box, so a route ending at S-19 has to light
    # the box that stands for it rather than an id that was never drawn.
    stands_for = {state_id: node.id for node in layout.nodes.values()
                  for state_id in (node.merged_ids or (node.id,))}
    drawn = {(edge.source, edge.target, edge.outcome) for edge in layout.edges}

    found: Dict[str, dict] = {}
    for scenario in scenarios:
        steps = list(getattr(scenario, "path", ()) or ())
        if not steps:
            continue

        walked = [(_START_ID, steps[0].decision_id, "")]
        for index, step in enumerate(steps):
            if index + 1 < len(steps):
                walked.append((step.decision_id, steps[index + 1].decision_id, step.variant))
            else:
                ending = stands_for.get(step.next_state)
                if ending:
                    walked.append((step.decision_id, ending, step.variant))

        edges = [edge for edge in walked if edge in drawn]
        if not edges:
            continue
        nodes = list(dict.fromkeys([end for edge in edges for end in edge[:2]]))
        # The block as well as the route. Which capability a scenario tests is the first thing a
        # reader wants from the collapsed drawing, and without this, opening a scenario while the
        # collapsed view is showing lights nothing at all -- which reads as the highlighting being
        # broken rather than as the two views having different vocabularies.
        found[scenario.id] = {"nodes": nodes, "edges": [list(edge) for edge in edges],
                              "block": getattr(scenario, "capability_id", "") or ""}
    return found


# The bands the paint can be asked to show, in the order the control offers them. One at a time,
# never blended: "where do the High-materiality scenarios come from" is a question with an answer,
# where "which tier dominates this arrow" is a summary of three numbers that hides all three.
#
# Kept as one tuple so the server's keys and the page's controls cannot drift apart.
BANDS = ("all", "High", "Medium", "Low")

# Each band twice: as counted, and with whatever the review flagged taken out. Per band rather
# than once overall, because taking the flagged scenarios out of the High band is a different
# subtraction from taking them out of the space.
LOADS = tuple(BANDS) + tuple(f"{band}_kept" for band in BANDS) + ("proposed", "flagged")


def load(intake: IntakeData, scenarios: Sequence) -> Dict[str, dict]:
    """How much of the scenario space runs through each part of the drawing."""
    walked = routes(intake, scenarios)
    by_id = {getattr(s, "id", ""): s for s in scenarios}
    owner = {d.id: d.trigger_capability for d in intake.decisions}

    # The collapsed drawing's own arrows, so folding the capabilities keeps the reading rather
    # than losing it. Indexed off the block layout rather than derived a second time: which pairs
    # of blocks are joined, and whether the join reads as a hand-off or a route back, is a
    # judgement :func:`build_block_layout` already makes.
    joins = {(edge.source, edge.target): edge.outcome
             for edge in build_block_layout(intake).edges}
    # Which block each state opens, so a route that *ends* by handing on is counted on the arrow
    # it hands on along. Every scenario is scoped to one block, so no route walks a hand-off from
    # the inside -- and left at that, the collapsed drawing would paint the endings and leave the
    # agent's main arteries blank, which reads as a fault rather than as a fact about scoping.
    entered_by: Dict[str, str] = {}
    for capability in intake.capabilities:
        for state_id in capability.entry_states:
            entered_by.setdefault(state_id, capability.id)

    tally: Dict[str, Dict[str, int]] = {}
    peak = {name: 0 for name in LOADS}

    def add(key: str, scenario) -> None:
        counts = tally.setdefault(key, {name: 0 for name in LOADS})
        tier = getattr(scenario, "review_materiality", "") or getattr(scenario, "materiality", "")
        flagged = bool((getattr(scenario, "review_flag", "") or "").strip())

        for band in ("all", tier if tier in BANDS else ""):
            if not band:
                continue
            counts[band] += 1
            if not flagged:
                counts[f"{band}_kept"] += 1
        if getattr(scenario, "origin", "") == ORIGIN_PROPOSED:
            counts["proposed"] += 1
        if flagged:
            counts["flagged"] += 1
        for name in LOADS:
            peak[name] = max(peak[name], counts[name])

    for scenario_id, where in walked.items():
        scenario = by_id.get(scenario_id)
        if scenario is None:
            continue
        for node_id in where.get("nodes", ()):
            add("node:" + node_id, scenario)
        for source, target, outcome in where.get("edges", ()):
            add("|".join(("edge", source, target, outcome)), scenario)
        block_id = where.get("block") or ""
        if block_id:
            add("block:" + block_id, scenario)

        for source, target in _block_hops(scenario, owner, joins, entered_by):
            add("|".join(("edge", source, target, joins[(source, target)])), scenario)

    return {"at": tally, "peak": peak}


def _block_hops(scenario, owner: Dict[str, str], joins: Dict[Tuple[str, str], str],
                entered_by: Dict[str, str]) -> List[Tuple[str, str]]:
    """Which arrows of the collapsed drawing one route travels."""
    steps = list(getattr(scenario, "path", ()) or ())
    if not steps:
        return []

    sequence = [owner.get(step.decision_id, "") for step in steps]
    hops: List[Tuple[str, str]] = []

    opening = next((block for block in sequence if block), "")
    if (_START_ID, opening) in joins:
        hops.append((_START_ID, opening))

    previous = ""
    for block in sequence:
        if block and previous and block != previous and (previous, block) in joins:
            hops.append((previous, block))
        if block:
            previous = block

    # And where it ends, which is one of two things. A route that stops at a terminal state ends
    # at a box hanging off its block; a route that stops at another block's entry has handed on,
    # and travels that arrow to do it.
    last = steps[-1].next_state
    ending = (previous, f"{previous}:{last}")
    if ending in joins:
        hops.append(ending)
    onward = entered_by.get(last, "")
    if onward and onward != previous and (previous, onward) in joins:
        hops.append((previous, onward))

    return list(dict.fromkeys(hops))


def _described(states: Dict[str, State], ids: Sequence[str]) -> List[dict]:
    """State ids with what each one is, for a picker. An id on its own is unreadable: the choice
    is made by reading the drawing, and "S-04" says nothing about which box that is."""
    return [{"id": state_id,
             "label": (states[state_id].description or state_id) if state_id in states
                      else "not in the graph",
             "terminal": bool(state_id in states and states[state_id].is_terminal),
             "missing": state_id not in states}
            for state_id in ids]


def _entry_shortlist(graph: DecisionGraph, capability, intake: IntakeData) -> List[str]:
    """What the *entered at* picker opens on."""
    found = sorted(set(capability.entry_states) |
                   set(entry_candidates(graph, capability.id, intake.decisions)))
    return found or list(graph.start_states)


def highlights(intake: IntakeData) -> Dict[str, Dict[str, dict]]:
    """What each row of the declaration points at in the drawing, by kind and key."""
    layout = build_layout(intake)
    drawn = {(edge.source, edge.target, edge.outcome) for edge in layout.edges}
    stands_for = {state_id: node.id for node in layout.nodes.values()
                  for state_id in (node.merged_ids or (node.id,))}

    def _edges(keep) -> List[list]:
        return [[e.source, e.target, e.outcome] for e in layout.edges
                if keep(e) and (e.source, e.target, e.outcome) in drawn]

    found: Dict[str, Dict[str, dict]] = {k: {} for k in
                                         ("capability", "decision", "state", "tool", "persona")}

    for decision in intake.decisions:
        found["decision"][decision.id] = {
            "nodes": [decision.id] if decision.id in layout.nodes else [],
            "edges": _edges(lambda e, d=decision.id: e.source == d or e.target == d)}

    for state in intake.states:
        box = stands_for.get(state.id)
        found["state"][state.id] = {
            "nodes": [box] if box in layout.nodes else [],
            "edges": _edges(lambda e, s=state.id: e.state_id == s)}

    for capability in intake.capabilities:
        inside = {d.id for d in intake.decisions if d.trigger_capability == capability.id}
        nodes = sorted(inside & set(layout.nodes))
        # The block itself, for the collapsed drawing. Lighting it there and its decisions here
        # means one selection reads in whichever view happens to be showing.
        nodes.append(capability.id)
        for state_id in tuple(capability.entry_states) + tuple(capability.exit_states):
            box = stands_for.get(state_id)
            if box in layout.nodes:
                nodes.append(box)
        found["capability"][capability.id] = {
            "nodes": sorted(set(nodes)),
            "edges": _edges(lambda e: e.source in inside or e.target in inside)}

    for tool in intake.tools:
        used_by = {d.id for d in intake.decisions
                   if tool.capability_id and d.trigger_capability == tool.capability_id}
        found["tool"][tool.name] = {
            "nodes": sorted(used_by & set(layout.nodes)),
            "edges": _edges(lambda e: e.source in used_by)}

    for persona in intake.personas:
        found["persona"][persona.id] = {"nodes": [], "edges": []}

    return found


def declaration(intake: IntakeData) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    """The intake as four readable lists: decisions with their states, capabilities, tools, and
    every state, for the pickers that set a capability's span.
    """
    graph = DecisionGraph(intake.decisions, intake.states)
    states = {s.id: s for s in intake.states}
    by_capability: Dict[str, List[str]] = {}

    rows = []
    for decision in intake.decisions:
        by_capability.setdefault(decision.trigger_capability, []).append(decision.id)
        outcomes = []
        for variant in decision.variants:
            target = graph.successor(decision.id, variant)
            state = states.get(target)
            outcomes.append({
                "variant": variant,
                "state_id": state.id if state else "",
                "state": (state.description or state.id) if state else "nothing declared",
                "terminal": bool(state and state.is_terminal),
                "outcome_type": state.outcome_type if state else "",
                "dangling": state is None,
            })
        rows.append({"id": decision.id, "name": decision.name or decision.id,
                     "capability_id": decision.trigger_capability,
                     "input_source": decision.input_source,
                     "attempts": decision.max_attempts,
                     "condition": decision.outcome_condition,
                     "outcomes": outcomes,
                     "out_of_scope": decision.out_of_scope})

    capabilities = [{
        "id": capability.id,
        "name": capability.name or capability.id,
        "type": capability.type,
        # A capability nothing branches on contributes no scenarios, and one with no type silently
        # drops the probes that would have tested it. Both are worth seeing without opening a file.
        "decisions": by_capability.get(capability.id, []),
        "tools": [t.name for t in intake.tools if t.capability_id == capability.id],
        # The block this capability covers. Shown as the states themselves rather than as ids,
        # because the choice is made by reading the drawing and "S-04" means nothing without it.
        "entry_states": list(capability.entry_states),
        "exit_states": list(capability.exit_states),
        "is_bounded": capability.is_bounded,
        # Where a route entering here would leave, worked out from the graph. Offered rather than
        # applied: a block whose decisions are reachable from outside it derives badly, and a
        # validator may want a block to stop earlier than the graph implies. Both are real, so
        # this is a proposal somebody accepts in one click and can then correct.
        "derived_exits": _described(states, exits_for(
            graph, capability.id, capability.entry_states or
            entry_candidates(graph, capability.id, intake.decisions)[:1], intake.decisions)),
        # The shortlists the pickers open on. On a real declaration this is the difference between
        # three rows and twenty-eight, twice over, per capability.
        #
        # Whatever is already drawn belongs in it whether or not the shortlist would have
        # suggested it -- a span may legitimately begin anywhere, and a boundary that has to be
        # hunted for in the long list every time reads as one the control refuses to hold. The
        # exits have always done this; the entries had not, which is half of why they felt
        # different to use.
        "entry_options": _described(states, _entry_shortlist(graph, capability, intake)),
        "exit_options": _described(states, sorted(set(capability.exit_states) | set(exits_for(
            graph, capability.id,
            capability.entry_states or entry_candidates(
                graph, capability.id, intake.decisions)[:1], intake.decisions)))),
    } for capability in intake.capabilities]

    # Every state, for the two pickers that set a span. Ordered as the intake declares them, so
    # the list reads down the flow rather than alphabetically.
    state_options = [{"id": state.id,
                      "label": f"{state.id} · {state.description or 'no description'}"
                               + (" · ends" if state.is_terminal else ""),
                      "label_text": state.description or "no description",
                      "terminal": state.is_terminal}
                     for state in intake.states]

    known = {c.id for c in intake.capabilities}
    tool_rows = [{
        "name": tool.name,
        "capability_id": tool.capability_id,
        "state_changing": tool.state_changing,
        "unlinked": bool(tool.capability_id) and tool.capability_id not in known,
    } for tool in intake.tools]
    return rows, capabilities, tool_rows, state_options
