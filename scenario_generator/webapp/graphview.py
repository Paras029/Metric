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
from typing import Dict, List, Optional, Sequence

from ..core.models import Decision, IntakeData, State

BOX_WIDTH = 190
BOX_HEIGHT = 62
GAP_X = 40
GAP_Y = 104
MARGIN = 28
MAX_LABEL = 22

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


def _decision_detail(decision: Decision) -> str:
    parts = [f"{decision.id}: {decision.name}",
             f"outcomes: {' / '.join(decision.variants) or 'none declared'}"]
    if decision.trigger_capability:
        parts.append(f"capability {decision.trigger_capability}")
    if decision.input_source:
        parts.append(f"input from {decision.input_source}")
    if decision.max_attempts > 1:
        parts.append(f"up to {decision.max_attempts} attempts")
    if decision.outcome_condition:
        parts.append(f"when {decision.outcome_condition}")
    return " · ".join(parts)


def _state_detail(state: State) -> str:
    detail = f"{state.id}: {state.description or 'no description'}"
    if state.is_terminal:
        detail += " · ends the interaction"
        if state.outcome_type:
            detail += f" ({state.outcome_type})"
    return detail


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
            detail=_decision_detail(decision), pending=decision.id in pending_decisions)

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
                    detail=f"{decision.name} → {variant} → {_state_detail(state)}",
                    pending=pending))
                continue
            for decision_id in state.next_decisions:
                if decision_id not in layout.nodes:
                    continue
                layout.edges.append(Edge(
                    source=decision.id, target=decision_id, outcome=variant,
                    state_label=state.description or state.id,
                    detail=f"{decision.name} → {variant} → {_state_detail(state)}",
                    pending=pending))

    _assign_depths(layout)

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
    layout.width = MARGIN * 2 + widest * BOX_WIDTH + (widest - 1) * GAP_X
    layout.height = MARGIN * 2 + len(rows) * BOX_HEIGHT + (len(rows) - 1) * GAP_Y

    for depth, row in sorted(rows.items()):
        row.sort(key=lambda n: (n.kind == TERMINAL, n.id))
        span = len(row) * BOX_WIDTH + (len(row) - 1) * GAP_X
        left = (layout.width - span) / 2
        for column, node in enumerate(row):
            node.x = left + column * (BOX_WIDTH + GAP_X)
            node.y = MARGIN + depth * (BOX_HEIGHT + GAP_Y)


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


def _edge_path(source: Node, target: Node) -> str:
    x1, y1 = source.x + BOX_WIDTH / 2, source.y + BOX_HEIGHT
    x2, y2 = target.x + BOX_WIDTH / 2, target.y
    if abs(x1 - x2) < 1:
        return f"M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}"
    midpoint = (y1 + y2) / 2
    return (f"M {x1:.1f} {y1:.1f} C {x1:.1f} {midpoint:.1f} "
            f"{x2:.1f} {midpoint:.1f} {x2:.1f} {y2:.1f}")


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
        classes = "graph__edge" + (" graph__edge--pending" if edge.pending else "")
        parts.append(
            f'<g class="{classes}"><title>{html.escape(edge.detail)}</title>'
            f'<path d="{_edge_path(source, target)}" marker-end="url(#arrow)"/></g>')

    # Labels are drawn after every edge so no path crosses over the text.
    for edge in layout.edges:
        source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
        if not source or not target:
            continue
        label = _edge_label(edge)
        mid_x = (source.x + target.x) / 2 + BOX_WIDTH / 2
        mid_y = (source.y + BOX_HEIGHT + target.y) / 2
        width = len(label) * 5.6 + 14
        parts.append(
            f'<g class="graph__label"><title>{html.escape(edge.detail)}</title>'
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
        detail = node.detail
        if node.id in layout.unreachable:
            classes.append("graph__node--unreachable")
            detail += " · not reachable from the start"
        elif node.id in layout.orphans:
            classes.append("graph__node--unreachable")
            detail += " · not connected to anything yet"

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
        "unreachable": layout.unreachable,
        "orphans": layout.orphans,
        "depth": max((n.depth for n in layout.nodes.values()), default=0) + 1,
    }


def completeness(intake: IntakeData) -> List[Dict[str, str]]:
    """What is wrong with the declaration, as things a person can go and fix.

    Every one of these silently narrows the benchmark rather than breaking it, which is why they
    are worth stating: a decision with one outcome contributes no branch, and a route that leads
    nowhere is a part of the agent that is never walked.
    """
    problems: List[Dict[str, str]] = []
    layout = build_layout(intake)

    for decision in intake.decisions:
        if len(decision.variants) < 2:
            problems.append({
                "id": decision.id,
                "what": f"{decision.id} ({decision.name}) has "
                        f"{'no' if not decision.variants else 'only one'} named outcome",
                "why": "A branch point with fewer than two outcomes adds no routes, so nothing "
                       "here gets tested. Name what it decides between."})
        unresolved = [v for v in decision.variants if _target_of(intake, decision.id, v) is None]
        if unresolved:
            problems.append({
                "id": decision.id,
                "what": f"{decision.id} outcome"
                        f"{'s' if len(unresolved) > 1 else ''} "
                        f"{', '.join(unresolved)} lead nowhere",
                "why": "No state is reached via this outcome, so the route stops here. Add a "
                       "state whose 'reached via' is "
                       f"{decision.id}={unresolved[0]}."})

    for state_id in layout.unreachable:
        problems.append({
            "id": state_id,
            "what": f"{state_id} cannot be reached",
            "why": "Nothing leads to this state, so no scenario ever ends here. Usually a "
                   "'reached via' that does not match any declared outcome."})

    if not _start_states(intake):
        problems.append({
            "id": "",
            "what": "No state is marked as the start",
            "why": "One state needs 'Start' in its 'reached via' column, or the graph has no "
                   "entry point and nothing can be walked."})

    declared = {c.id for c in intake.capabilities}
    used = {d.trigger_capability for d in intake.decisions if d.trigger_capability}
    for capability in sorted(declared - used):
        problems.append({
            "id": capability,
            "what": f"{capability} has no decisions",
            "why": "A capability nothing branches on contributes no scenarios. Either it needs a "
                   "decision, or it is not really a separate capability."})

    return problems
