"""Drawing the declared decision graph as an SVG.

These graphs get complicated quickly, and a picture of one is only worth having if it stays
readable when it does. Three choices follow from that.

The layout is layered top-down by distance from the start state, because the thing a reader wants
from this picture is how far a route runs and where it ends, and depth read vertically answers
both at a glance.

States are boxes and decision outcomes are the edges between them. That matches how the intake
describes the agent, so what is on screen and what is in the workbook stay recognisably the same
object -- important, since the workbook is what the reader edits.

The SVG is generated here rather than by a drawing library. It renders offline with no script and
no font download, degrades to plain markup a browser can print, and the hover detail is a native
SVG title, which works without JavaScript and is read out by screen readers.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.models import IntakeData, State

BOX_WIDTH = 190
BOX_HEIGHT = 62
GAP_X = 34
GAP_Y = 92
MARGIN = 28
MAX_LABEL = 22


@dataclass
class Node:
    state: State
    depth: int = 0
    x: float = 0.0
    y: float = 0.0
    column: int = 0


@dataclass
class Edge:
    source: str
    target: str
    decision_id: str
    variant: str
    label: str = ""
    detail: str = ""


@dataclass
class Layout:
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    unreachable: List[str] = field(default_factory=list)


def _start_states(intake: IntakeData) -> List[State]:
    starts = [s for s in intake.states if s.reached_via.strip().lower() == "start"]
    return starts or (intake.states[:1] if intake.states else [])


def _target_of(intake: IntakeData, decision_id: str, variant: str) -> Optional[State]:
    """The state a decision outcome leads to, matched on the intake's own 'DEC-xx=Variant' form."""
    wanted = f"{decision_id}={variant}".strip().lower()
    for state in intake.states:
        if state.reached_via.strip().lower().replace(" ", "") == wanted.replace(" ", ""):
            return state
    return None


def build_layout(intake: IntakeData) -> Layout:
    """Place every state by its distance from the start, and connect them by decision outcome."""
    layout = Layout()
    if not intake.states:
        return layout

    by_id = {s.id: s for s in intake.states}
    decisions = {d.id: d for d in intake.decisions}

    for state in intake.states:
        layout.nodes[state.id] = Node(state=state)

    # Breadth-first from the start gives each state a depth; anything the walk never reaches is
    # reported rather than dropped, since an unreachable state is a defect in the declaration and
    # the reader should see it.
    frontier = [(s.id, 0) for s in _start_states(intake)]
    seen = {state_id for state_id, _ in frontier}
    while frontier:
        state_id, depth = frontier.pop(0)
        node = layout.nodes.get(state_id)
        if node is None:
            continue
        node.depth = depth
        for decision_id in by_id[state_id].next_decisions:
            decision = decisions.get(decision_id)
            if decision is None:
                continue
            for variant in decision.variants:
                target = _target_of(intake, decision_id, variant)
                if target is None:
                    continue
                detail = f"{decision.name} — outcome “{variant}”"
                if decision.input_source:
                    detail += f" · input from {decision.input_source}"
                if decision.max_attempts > 1:
                    detail += f" · up to {decision.max_attempts} attempts"
                if decision.outcome_condition:
                    detail += f" · when {decision.outcome_condition}"
                layout.edges.append(Edge(
                    source=state_id, target=target.id, decision_id=decision_id,
                    variant=variant, label=f"{decision_id} · {variant}", detail=detail))
                if target.id not in seen:
                    seen.add(target.id)
                    frontier.append((target.id, depth + 1))

    layout.unreachable = [s.id for s in intake.states if s.id not in seen]
    for index, state_id in enumerate(layout.unreachable):
        # Park unreachable states on a row of their own beneath the graph.
        layout.nodes[state_id].depth = max((n.depth for n in layout.nodes.values()), default=0) + 1
        layout.nodes[state_id].column = index

    rows: Dict[int, List[Node]] = {}
    for node in layout.nodes.values():
        rows.setdefault(node.depth, []).append(node)

    widest = max((len(row) for row in rows.values()), default=1)
    layout.width = MARGIN * 2 + widest * BOX_WIDTH + (widest - 1) * GAP_X
    layout.height = MARGIN * 2 + len(rows) * BOX_HEIGHT + (len(rows) - 1) * GAP_Y

    for depth, row in sorted(rows.items()):
        row.sort(key=lambda n: n.state.id)
        span = len(row) * BOX_WIDTH + (len(row) - 1) * GAP_X
        left = (layout.width - span) / 2
        for column, node in enumerate(row):
            node.column = column
            node.x = left + column * (BOX_WIDTH + GAP_X)
            node.y = MARGIN + depth * (BOX_HEIGHT + GAP_Y)

    return layout


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


def render_svg(intake: IntakeData) -> str:
    """The declared graph as a self-contained SVG. Returns an empty string for an empty intake."""
    layout = build_layout(intake)
    if not layout.nodes:
        return ""

    parts = [
        f'<svg class="graph" viewBox="0 0 {layout.width:.0f} {layout.height:.0f}" '
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
        parts.append(
            f'<g class="graph__edge"><title>{html.escape(edge.detail)}</title>'
            f'<path d="{_edge_path(source, target)}" marker-end="url(#arrow)"/></g>')

    # Labels are drawn after every edge so no path crosses over the text.
    for edge in layout.edges:
        source, target = layout.nodes.get(edge.source), layout.nodes.get(edge.target)
        if not source or not target:
            continue
        mid_x = (source.x + target.x) / 2 + BOX_WIDTH / 2
        mid_y = (source.y + BOX_HEIGHT + target.y) / 2
        label = html.escape(edge.label)
        width = len(edge.label) * 5.6 + 12
        parts.append(
            f'<g class="graph__label"><title>{html.escape(edge.detail)}</title>'
            f'<rect x="{mid_x - width / 2:.1f}" y="{mid_y - 9:.1f}" width="{width:.1f}" '
            f'height="17" rx="8.5"/>'
            f'<text x="{mid_x:.1f}" y="{mid_y + 3.5:.1f}" text-anchor="middle">{label}</text></g>')

    for node in layout.nodes.values():
        state = node.state
        classes = ["graph__node"]
        if state.is_terminal:
            classes.append("graph__node--terminal")
            if state.outcome_type:
                classes.append(f"graph__node--{state.outcome_type.split()[0].lower()}")
        if state.id in layout.unreachable:
            classes.append("graph__node--unreachable")

        detail = f"{state.id}: {state.description or 'no description'}"
        if state.is_terminal:
            detail += f" · ends the interaction{f' ({state.outcome_type})' if state.outcome_type else ''}"
        if state.id in layout.unreachable:
            detail += " · not reachable from the start state"

        lines = _wrap(state.description or state.id)
        text_y = node.y + (BOX_HEIGHT / 2) - (len(lines) - 1) * 6 + 1
        spans = "".join(
            f'<tspan x="{node.x + BOX_WIDTH / 2:.1f}" dy="{0 if i == 0 else 12}">'
            f'{html.escape(line)}</tspan>' for i, line in enumerate(lines))

        parts.append(
            f'<g class="{" ".join(classes)}"><title>{html.escape(detail)}</title>'
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{BOX_WIDTH}" height="{BOX_HEIGHT}" '
            f'rx="6"/>'
            f'<text class="graph__id" x="{node.x + 10:.1f}" y="{node.y + 15:.1f}">'
            f'{html.escape(state.id)}</text>'
            f'<text class="graph__caption" y="{text_y:.1f}">{spans}</text></g>')

    parts.append("</svg>")
    return "".join(parts)


def graph_summary(intake: IntakeData) -> Dict[str, object]:
    """Counts worth stating beside the picture, including anything the declaration got wrong."""
    layout = build_layout(intake)
    terminal = [s for s in intake.states if s.is_terminal]
    return {
        "states": len(intake.states),
        "terminal": len(terminal),
        "decisions": len(intake.decisions),
        "outcomes": sum(len(d.variants) for d in intake.decisions),
        "unreachable": layout.unreachable,
        "depth": max((n.depth for n in layout.nodes.values()), default=0) + 1,
    }
