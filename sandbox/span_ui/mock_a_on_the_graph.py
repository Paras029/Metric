"""A. Draw the boundary on the drawing.

The span is a fact about the graph, and the graph is already on the page directly above this
panel. Asking somebody to describe a boundary in a list of ids when the picture of it is six
inches away is the whole of the present problem.
"""
from _shell import CAPABILITIES, page

CSS = """
.split { display: grid; grid-template-columns: 1fr 20rem; gap: var(--s5); align-items: start; }
.canvas { border: 1px solid var(--rule); border-radius: var(--r-md); background: var(--canvas);
          padding: var(--s4); }
.node { fill: var(--surface); stroke: var(--rule-strong); stroke-width: 1; }
.node--in { fill: var(--blue-tint); stroke: var(--blue); stroke-width: 2; }
.node--out { fill: #fff; stroke: var(--blue); stroke-width: 2; stroke-dasharray: 4 3; }
.node--armed { fill: var(--blue-tint); stroke: var(--blue); stroke-width: 1.5;
               stroke-dasharray: 3 3; }
.node--dim { opacity: .22; }
.nlabel { font: 600 9px var(--font-mono); fill: var(--ink); }
.ntext { font: 8.5px var(--font-sans); fill: var(--ink-mid); }
.edge { stroke: var(--rule-strong); fill: none; }
.hull { fill: rgba(0,111,207,.05); stroke: var(--blue-edge); stroke-width: 1;
        stroke-dasharray: 5 4; }
.hull__tag { font: 700 9px var(--font-sans); fill: var(--blue-dark); letter-spacing: .06em; }
.arm { display: flex; gap: var(--s2); margin: 0 0 var(--s3); }
.arm button { flex: 1; }
.arm .is-on { background: var(--blue); border-color: var(--blue-dark); color: #fff;
              font-weight: 600; }
.picked { list-style: none; margin: 0 0 var(--s3); padding: 0; }
.picked li { display: flex; align-items: center; gap: var(--s2); padding: .3rem 0;
             border-bottom: 1px solid var(--rule-soft); font-size: var(--t-small); }
.picked .mono { color: var(--blue-dark); font-weight: 600; }
.picked .drop { margin-left: auto; border: 0; background: none; color: var(--ink-dim);
                cursor: pointer; font-size: var(--t-meta); }
.picked--empty { color: var(--ink-dim); font-size: var(--t-small); font-style: italic;
                 padding: .3rem 0; }
.side__label { font-size: var(--t-micro); font-weight: 700; letter-spacing: .1em;
               text-transform: uppercase; color: var(--ink-dim); margin: 0 0 var(--s2); }
.hint { font-size: var(--t-meta); color: var(--ink-dim); margin: 0 0 var(--s4);
        padding: var(--s2) var(--s3); background: var(--blue-tint);
        border: 1px solid var(--blue-edge); border-radius: var(--r-sm); }
.picker { border: 1px solid var(--rule); border-radius: var(--r-md); padding: var(--s4);
          background: var(--surface); }
"""

# (id, x, y, label, class) -- laid out by hand; this is a mockup, not the real layout engine.
NODES = [
    ("S-00", 150, 34, "Chat opens", "node--in"),
    ("DEC-01", 150, 86, "Identify the cardmember", ""),
    ("S-02", 290, 86, "Card not recognised", ""),
    ("S-03", 20, 138, "Identified by card", "node--out"),
    ("S-04", 150, 138, "Identified by code", "node--out"),
    ("S-05", 280, 138, "Not identified", "node--out"),
    ("DEC-03", 20, 214, "Verify the account", "node--dim"),
    ("S-08", 20, 266, "Verified", "node--dim"),
    ("S-09", 150, 266, "Passed to an adviser", "node--dim"),
]

# Straight drops and elbows only -- a mockup that needs a routing algorithm to look right is
# testing the routing algorithm rather than the control.
EDGES = [
    "M180 64 L180 82",                       # S-00   -> DEC-01
    "M180 116 L180 134",                     # DEC-01 -> S-04
    "M160 116 L75 128 L75 134",              # DEC-01 -> S-03
    "M245 101 L286 101",                     # DEC-01 -> S-02
    "M320 116 L320 126 L335 126 L335 134",   # S-02   -> S-05
    "M75 168 L75 210",                       # S-03   -> DEC-03
    "M75 244 L75 262",                       # DEC-03 -> S-08
    "M115 229 L205 229 L205 262",            # DEC-03 -> S-09
]

def _graph() -> str:
    parts = ['<text class="hull__tag" x="12" y="14">CAP-01 IDENTIFICATION</text>',
             '<rect class="hull" x="8" y="22" width="394" height="156" rx="4"/>']
    for path in EDGES:
        parts.append(f'<path class="edge" d="{path}"/>')
    for sid, x, y, text, klass in NODES:
        parts.append(
            f'<g><rect class="node {klass}" x="{x}" y="{y}" width="110" height="30" rx="2"/>'
            f'<text class="nlabel" x="{x + 8}" y="{y + 13}">{sid}</text>'
            f'<text class="ntext" x="{x + 8}" y="{y + 25}">{text}</text></g>')
    return ('<svg viewBox="0 0 410 306" width="100%" role="img" '
            'aria-label="The declared graph, with CAP-01 outlined">'
            + "".join(parts) + "</svg>")


def _chips(items, kind):
    if not items:
        return f'<p class="picked--empty">Nothing chosen &mdash; click a box to set the {kind}.</p>'
    rows = "".join(f'<li><span class="mono">{i}</span> {t}'
                   f'<button class="drop">remove</button></li>' for i, t in items)
    return f'<ul class="picked">{rows}</ul>'


def build() -> str:
    cap = CAPABILITIES[0]
    return page("A — draw the boundary on the drawing", f"""
      <div class="sheet">
        <h1>Capabilities</h1>
        <p class="lede">A &mdash; the boundary is drawn on the graph, not described in a list.</p>
        <div class="split">
          <div class="canvas">{_graph()}</div>
          <div class="picker">
            <p class="hint"><b>Setting where Identification is entered.</b>
              Click a box in the drawing. Escape, or the button again, to stop.</p>
            <div class="arm">
              <button class="button is-on">Set where it starts</button>
              <button class="button">Set where it ends</button>
            </div>
            <p class="side__label">Entered at</p>
            {_chips([("S-00", "The chat opens")], "start")}
            <p class="side__label">Hands on or ends at</p>
            {_chips([("S-03", "Identified from card details"),
                     ("S-04", "Identified by one-time code"),
                     ("S-05", "Not identified; chat ended")], "ending")}
            <button class="button button--primary">Save span</button>
          </div>
        </div>
        <ul class="caps" style="margin-top:var(--s5)">
          <li><div class="cap__head"><span class="mono">CAP-02</span>
            <span class="cap__name">Verification</span>
            <span class="mark">Gating</span>
            <span class="cap__note" style="margin:0">S-03, S-04 &rarr; S-08, S-09</span>
            <button class="button button--quiet" style="margin-left:auto">Redraw</button>
          </div></li>
          <li><div class="cap__head"><span class="mono">CAP-03</span>
            <span class="cap__name">Charge handling</span>
            <span class="mark">Transactional</span>
            <span class="cap__note" style="margin:0">S-08 &rarr; S-12, S-13</span>
            <button class="button button--quiet" style="margin-left:auto">Redraw</button>
          </div></li>
        </ul>
      </div>
      <p class="legend"><b>How it reads.</b> One capability is being drawn at a time; the rest
      collapse to a single line with their span stated as <span class="mono">entry &rarr;
      exits</span>. The graph outlines the block being drawn, dims what is outside it, and marks
      the two kinds of boundary differently &mdash; solid for the way in, dashed for the ways out.
      <b>What it costs:</b> the graph has to accept clicks and report them, which is the largest
      code change of the three.</p>""", CSS)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    Path(sys.argv[1]).write_text(build(), encoding="utf-8")
