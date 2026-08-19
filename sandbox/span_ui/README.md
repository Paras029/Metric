# Capability spans: three ways to draw the boundary

**Nothing here is wired into the tool.** This directory is a sandbox: three self-contained HTML
mockups of the same panel, built against the real design tokens so the comparison is fair, plus
the script that renders them to PNG.

    python sandbox/span_ui/render.py

## The problem being solved

Today the Capabilities section on the intake stage shows, per capability, two scrolling lists of
**every state in the graph** — once for "Entered at" and once for "Hands on or ends at". On the
five-state examples that reads fine. On a real declaration it does not: twenty-eight states and
five capabilities is 280 checkboxes on one page, in ten identical scrolling boxes, and the state
that matters is somewhere in the middle of one of them.

The clutter is a symptom. The real problem is that the control asks the wrong question. It asks
"which of these twenty-eight states, twice" when what the validator knows is "identification runs
from the opening of the chat until it hands on".

## What each mockup proposes

| | Idea | Choices per capability |
|---|---|---|
| **A** | Pick on the drawing | 1 click per boundary, on the graph itself |
| **B** | Name the entry, derive the exits | **1** |
| **C** | The same lists, pruned to candidates | 2 short lists instead of 2 long ones |

Read them in that order; each is a bigger change than the last in what it asks of the code, and
B is the smallest in what it asks of the person.

## The recommendation

**B, with C as its override.**

B is the only one that changes the question being asked. The exits are not a second judgement a
person is holding — they are a consequence of the entry and the decisions the capability owns, and
the tool can work them out: walk forward from the entry through this capability's decisions, and
every state the walk reaches that offers none of them is an exit. Asking for them by hand is
asking somebody to compute something and then type the answer in.

That makes the ordinary interaction one dropdown per capability instead of two lists of fourteen
checkboxes, and it makes the panel readable at a glance, because a capability that is settled
collapses to a line of chips — `S-03 S-04 → S-08 S-09` — that reads as the shape of the block
rather than as a form somebody filled in.

The derivation must be shown, not hidden, and it must be overridable. Two cases break it: a
capability whose decisions are also reachable from outside it, and a capability whose exit a
validator wants to draw tighter than the graph implies (stopping a block early on purpose). Both
are real, so *Set them by hand instead* opens exactly the control C proposes, pruned to candidates
rather than the whole graph.

A is the most direct expression of what a span is, and it is the right answer eventually — the
graph is already on the page and the boundary is a fact about it. It is also the largest change:
the SVG has to accept clicks, report which node was hit, hold an arming mode, and stay in step
with the form. It is worth doing after B, not instead of it.

## What none of these fixes

The panel is only half the problem. The other half is that a validator has to know **which**
capabilities are worth bounding at all before any of this helps, and nothing on the page says so.
A capability holding one decision does not want a span; the four-block chain in
`examples/intakes/5_wide_chain_scale.xlsx` is where the whole feature pays for itself. A line per
capability saying what its span would do to the scenario count — *"bounding this splits 200 routes
into 29"* — would change more decisions than any of the three layouts above.
