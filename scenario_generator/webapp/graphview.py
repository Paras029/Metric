"""Drawing the declared decision graph as an SVG.

**Boxes are decisions. Arrows are the states they lead to.** That is the way the intake is
actually authored — a state's `reached_via` is written as `DEC-01=Pass`, so a state is defined by
the decision outcome that produces it — and it is how a modelling team draws its own agent: the
boxes are the points where something is decided, and what runs between them is where the
interaction has got to.

Three node kinds. A start node for where the interaction opens, a decision node for every branch
point, and a terminal node for each way the interaction can end. Everything else is an edge, and
every edge carries the outcome that was taken and the state it lands in.

Pending additions -- things a person has added in the interface but not yet committed to the
workbook -- are drawn dashed. Ones that have been attached to something sit in the flow where
they will land; ones that have not are parked in a row underneath, which is the visible difference
between an edit that is ready and an edit that still needs somewhere to go.

The SVG is generated here rather than by a drawing library. It renders offline with no script and
no font download, the hover detail is a native SVG title that works without JavaScript and is read
out by screen readers, and the zoom control on the page is a transform over the top of it.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.models import Decision, IntakeData, State

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
LOOP_MARGIN = 34

START = "start"
DECISION = "decision"
TERMINAL = "terminal"
ORPHAN = "orphan"

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


@dataclass
class Edge:
    """One arrow: the state the interaction reaches, and the outcome that took it there."""

    source: str
    target: str
    outcome: str
    state_label: str
    detail: str
    pending: bool = False

    out_of_scope: bool = False
    """Whether this edge leaves a decision marked out of scope -- see :class:`Node`. Drawn muted
    rather than left off: the route is declared, it is just not one the benchmark walks."""

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


def _start_states(intake: IntakeData) -> List[State]:
    starts = [s for s in intake.states if s.reached_via.strip().lower() == "start"]
    return starts or (intake.states[:1] if intake.states else [])


def _target_of(intake: IntakeData, decision_id: str, variant: str) -> Optional[State]:
    """The state a decision outcome leads to, matched on the intake's own 'DEC-xx=Variant' form."""
    wanted = f"{decision_id}={variant}".strip().lower().replace(" ", "")
    for state in intake.states:
        if state.reached_via.strip().lower().replace(" ", "") == wanted:
            return state
    return None


def _panel(heading: str, rows: Sequence[Tuple[str, str]], footer: str = "") -> str:
    """Hover text as a small labelled panel rather than one run-on line.

    A tooltip is the only place most of the intake's detail is ever read, and everything the old
    single line separated with a middle dot -- id, name, outcomes, capability, input source,
    retry bound, condition -- had to be picked apart by eye every time. One fact per line, each
    named, is the same information at a fraction of the reading effort.

    Rows with no value are dropped rather than shown empty: a blank "Capability:" says nothing
    except that the panel was generated by a template.
    """
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


def _state_detail(state: State) -> str:
    return _panel(
        f"{state.id} · {state.description or 'no description'}",
        [("Reached via", state.reached_via)],
        footer=_ends_note(state))


def _edge_detail(decision: Decision, variant: str, state: State) -> str:
    """One arrow: the outcome taken, and the position it leads to."""
    return _panel(
        f"{decision.id} · {decision.name} → {variant}",
        [("Leads to", f"{state.id} · {state.description}" if state.description else state.id)],
        footer=_ends_note(state))


def build_layout(intake: IntakeData, pending_decisions: Sequence[str] = (),
                 pending_states: Sequence[str] = ()) -> Layout:
    """Place every decision by its distance from the start, and connect them by the states between.

    ``pending_decisions`` and ``pending_states`` are ids the caller has merged into the intake but
    not yet written to the workbook; they are drawn dashed. Anything that could not be placed in
    the flow -- a pending decision nothing leads to, or a state whose route does not resolve --
    ends up in :attr:`Layout.orphans` and is parked below the graph rather than dropped.
    """
    layout = Layout()
    if not intake.decisions and not intake.states:
        return layout

    pending_decisions, pending_states = set(pending_decisions), set(pending_states)
    starts = _start_states(intake)

    layout.nodes[_START_ID] = Node(
        id=_START_ID, kind=START, title="Start",
        caption=(starts[0].description if starts else "The interaction opens"),
        detail="Where the interaction opens" + (f" ({starts[0].id})" if starts else ""))

    for decision in intake.decisions:
        layout.nodes[decision.id] = Node(
            id=decision.id, kind=DECISION, title=decision.id, caption=decision.name,
            detail=_decision_detail(decision), pending=decision.id in pending_decisions,
            out_of_scope=decision.out_of_scope)

    for state in intake.states:
        if state.is_terminal:
            layout.nodes[state.id] = Node(
                id=state.id, kind=TERMINAL, title=state.id, caption=state.description,
                detail=_state_detail(state), pending=state.id in pending_states,
                outcome_type=state.outcome_type)

    def _leaving(state: State, source: str, pending: bool) -> None:
        """Draw the arrow out of a state into each decision it leads to."""
        for decision_id in state.next_decisions:
            if decision_id not in layout.nodes:
                continue
            layout.edges.append(Edge(
                source=source, target=decision_id, outcome="",
                state_label=state.description or state.id,
                detail=_state_detail(state),
                pending=pending or state.id in pending_states))

    for state in starts:
        _leaving(state, _START_ID, False)

    for decision in intake.decisions:
        for variant in decision.variants:
            state = _target_of(intake, decision.id, variant)
            if state is None:
                continue
            pending = decision.id in pending_decisions or state.id in pending_states
            if state.is_terminal:
                layout.edges.append(Edge(
                    source=decision.id, target=state.id, outcome=variant,
                    state_label=state.description or state.id,
                    detail=_edge_detail(decision, variant, state),
                    pending=pending, out_of_scope=decision.out_of_scope))
                continue
            for decision_id in state.next_decisions:
                if decision_id not in layout.nodes:
                    continue
                layout.edges.append(Edge(
                    source=decision.id, target=decision_id, outcome=variant,
                    state_label=state.description or state.id,
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
        if s.is_terminal and s.id not in reachable and s.id in layout.nodes)
    layout.orphans = sorted(
        node.id for node in layout.nodes.values()
        if node.id not in reachable and node.kind in (DECISION, TERMINAL))
    for node_id in layout.orphans:
        layout.nodes[node_id].kind = ORPHAN if layout.nodes[node_id].pending else \
            layout.nodes[node_id].kind

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

    parked = max((n.depth for n in layout.nodes.values() if n.id in seen), default=0) + 1
    for node in layout.nodes.values():
        if node.id not in seen:
            node.depth = parked


def _place(layout: Layout) -> None:
    rows: Dict[int, List[Node]] = {}
    for node in layout.nodes.values():
        rows.setdefault(node.depth, []).append(node)

    widest = max((len(row) for row in rows.values()), default=1)
    content_width = MARGIN * 2 + widest * BOX_WIDTH + (widest - 1) * GAP_X
    layout.height = MARGIN * 2 + len(rows) * BOX_HEIGHT + (len(rows) - 1) * GAP_Y

    for depth, row in sorted(rows.items()):
        row.sort(key=lambda n: (n.kind == TERMINAL, n.id))
        span = len(row) * BOX_WIDTH + (len(row) - 1) * GAP_X
        left = (content_width - span) / 2
        for column, node in enumerate(row):
            node.x = left + column * (BOX_WIDTH + GAP_X)
            node.y = MARGIN + depth * (BOX_HEIGHT + GAP_Y)

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


def _label_position(edge: Edge, source: Node, target: Node) -> tuple:
    """Where an edge's outcome label sits -- along the lane for a loop, at the midpoint otherwise."""
    if edge.is_back:
        y1, y2 = source.y + BOX_HEIGHT / 2, target.y + BOX_HEIGHT / 2
        return edge.lane_x, (y1 + y2) / 2
    mid_x = (source.x + target.x) / 2 + BOX_WIDTH / 2
    mid_y = (source.y + BOX_HEIGHT + target.y) / 2
    return mid_x, mid_y


def _edge_label(edge: Edge) -> str:
    """What the arrow says: the state it leads to, and the outcome that took it there."""
    state = edge.state_label if len(edge.state_label) <= 26 else edge.state_label[:25] + "…"
    return f"{edge.outcome} → {state}" if edge.outcome else state


def render_svg(intake: IntakeData, pending_decisions: Sequence[str] = (),
               pending_states: Sequence[str] = ()) -> str:
    """The declared graph as a self-contained SVG. Returns an empty string for an empty intake."""
    layout = build_layout(intake, pending_decisions, pending_states)
    if not layout.nodes:
        return ""

    parts = [
        f'<svg class="graph" viewBox="0 0 {layout.width:.0f} {layout.height:.0f}" '
        f'width="{layout.width:.0f}" height="{layout.height:.0f}" '
        f'role="img" aria-label="The declared decision graph" '
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
        parts.append(
            f'<g class="{classes}"><title>{html.escape(edge.detail)}</title>'
            f'<path d="{_edge_path(edge, source, target)}" marker-end="url(#arrow)"/></g>')

    # Labels are drawn after every edge so no path crosses over the text. A loop's label is
    # rotated to run along its lane -- upright, it would need the label's full text width just to
    # fit between two lanes 30px apart, which no reasonable width leaves room for.
    for edge in layout.edges:
        source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
        if not source or not target:
            continue
        label = _edge_label(edge)
        mid_x, mid_y = _label_position(edge, source, target)
        width = len(label) * 5.6 + 14
        classes = "graph__label" + (" graph__label--loop" if edge.is_back else "")
        rotate = f' transform="rotate(-90 {mid_x:.1f} {mid_y:.1f})"' if edge.is_back else ""
        parts.append(
            f'<g class="{classes}"{rotate}><title>{html.escape(edge.detail)}</title>'
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

        lines = _wrap(node.caption or node.title)
        text_y = node.y + (BOX_HEIGHT / 2) - (len(lines) - 1) * 6 + 1
        spans = "".join(
            f'<tspan x="{node.x + BOX_WIDTH / 2:.1f}" dy="{0 if i == 0 else 12}">'
            f'{html.escape(line)}</tspan>' for i, line in enumerate(lines))

        parts.append(
            f'<g class="{" ".join(classes)}" data-node="{html.escape(node.id)}">'
            f'<title>{html.escape(detail)}</title>'
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{BOX_WIDTH}" height="{BOX_HEIGHT}" '
            f'rx="6"/>'
            f'<text class="graph__id" x="{node.x + 10:.1f}" y="{node.y + 15:.1f}">'
            f'{html.escape(node.title)}</text>'
            f'<text class="graph__caption" y="{text_y:.1f}">{spans}</text></g>')

    parts.append("</svg>")
    return "".join(parts)


def graph_summary(intake: IntakeData, pending_decisions: Sequence[str] = (),
                  pending_states: Sequence[str] = ()) -> Dict[str, object]:
    """Counts worth stating beside the picture, including anything the declaration got wrong."""
    layout = build_layout(intake, pending_decisions, pending_states)
    terminal = [s for s in intake.states if s.is_terminal]
    return {
        "states": len(intake.states),
        "terminal": len(terminal),
        "decisions": len(intake.decisions),
        "outcomes": sum(len(d.variants) for d in intake.decisions),
        "out_of_scope": sum(1 for d in intake.decisions if d.out_of_scope),
        "unreachable": layout.unreachable,
        "orphans": layout.orphans,
        "depth": max((n.depth for n in layout.nodes.values()), default=0) + 1,
    }
