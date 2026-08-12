WHAT THIS IS

This image is one of {{position}} submitted as documentation for an AI agent. An independent team
is working out from this documentation what the agent actually is, so it can be tested.

Read this image **on its own**. Do not guess at what the other images contain, and do not try to
complete a flow that runs off the edge — a later pass is given every image's reading together and
decides what joins to what. Your job is to record, completely and exactly, what is in front of you.

FIRST, SAY WHAT THIS PICTURE IS

Before anything else, decide what you are looking at, and put it in `depicts`:

- `workflow` — a flow: boxes joined by arrows, showing how an interaction proceeds. Read it as
  boxes and arrows, below.
- `other` — anything else. A screenshot of a screen, a table of thresholds or rules, an
  architecture picture of systems that is not a flow, an org chart, a slide of prose, a logo, a
  photograph of a whiteboard listing points rather than drawing a flow.

Put a one-line statement of what the picture actually shows in `subject`, whichever it is.

**`other` is a real answer and often the right one.** Not everything submitted as documentation is
a flow diagram, and forcing a table of decline codes into boxes and arrows invents a workflow the
agent does not have — which is worse than reporting no workflow at all, because the invented one
gets tested. If it is not a flow, leave `nodes` and `edges` empty and put what the picture
establishes into `observations`, where it still informs the reading.

A picture can also be **partly** a flow — a diagram with a legend beside it, or a flow drawn under
a title block. Read the flow part as boxes and arrows and put the rest in `observations`.

IF IT IS A WORKFLOW

Not a description. A list of the boxes and a list of the arrows, each one its own entry.

That is deliberate: a diagram written out as a paragraph loses boxes silently, and nobody can
tell afterwards which ones. Written out as entries, a box that was missed shows up as an arrow
pointing at nothing, and can be asked about.

**Every box gets a node.** Give each one a short reference of your own (`n1`, `n2`, …), the label
exactly as written on it, and what kind of thing it is:

- `start` — where the interaction opens.
- `decision` — a branch point. More than one arrow leaves it, usually a diamond.
- `state` — a step or position the interaction passes through and then leaves.
- `terminal` — the flow stops here: a hand-off to a person, a rejection, a completed outcome.
- `system` — a system, service or datastore the flow calls rather than a step in the conversation.
- `other` — anything else drawn: a legend, a note, an annotation.

**Every arrow gets an edge**: which node it leaves, which node it enters, and its label exactly
as written. An unlabelled arrow gets an empty label — do not invent one.

**Every arrow that leaves the page gets an entry in `continues_offpage`** instead: which node it
leaves, its label, and which edge of the image it runs off. This is the single most important
thing to record accurately, because it is where this image joins another one, and a flow that
does not join up is a flow the scenario space cannot walk.

**Anything you cannot read goes in `unreadable`** — a label too small to make out, a box cut off
at the margin, an arrow whose destination is ambiguous. Say what and where. Never guess at a
label: a wrong outcome name becomes a test of something that does not exist.

Use the words on the diagram. If a box says "Auth check", write "Auth check", not "identity
verification" — the rest of the documentation will use the diagram's own words, and a tidied
label stops the two matching up.

Finally, count what you found and put the numbers in `counts`. Count first, then check your lists
have that many entries. This is a check on yourself, and a dense diagram is exactly where a
reading quietly stops short. For an `other` picture the counts are zero, which is not a failure.

WHAT THE PICTURE ESTABLISHES, IN PROSE

Whether or not it is a flow, note anything the image says that a reader of the documentation
should know, as `observations`. For an `other` picture this is the whole of your answer: the
thresholds in the table, what the screen offers the user, which systems the architecture names.
Quote the words on the image and say where on it they are.

THE SOURCE

Image file: {{filename}} (image {{position}})

OUTPUT

Return ONLY a JSON object of the form:

{"depicts": "workflow",
 "subject": "...",
 "nodes": [{"ref": "n1", "label": "...", "kind": "decision"}],
 "edges": [{"from": "n1", "to": "n2", "label": "Pass"}],
 "continues_offpage": [{"from": "n5", "label": "Fail", "side": "bottom"}],
 "observations": [{"statement": "...", "quote": "...", "locator": "top left"}],
 "unreadable": ["..."],
 "counts": {"boxes": 0, "arrows": 0}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
