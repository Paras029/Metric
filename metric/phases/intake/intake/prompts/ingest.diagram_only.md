WHAT THIS IS

One workflow diagram, submitted as documentation for an AI agent. It is the only image in the
pack, so what is drawn here is the whole of what was drawn. An independent team is working out
from this documentation what the agent actually is, so it can be tested.

There is nothing to join it to and nothing continuing off the page into another picture. Read what
is in front of you and write it down in the vocabulary below, in one pass.

A workflow diagram *is* the decision and state sheets of that vocabulary: a box with branching
arrows is a decision, an arrow's label is an outcome, and the box it lands in is a state. Keep that
structure. Do not flatten it to a paragraph and rebuild a graph from the paragraph — a diagram
written out as prose loses boxes silently, and nobody can tell afterwards which ones.

BEFORE YOU WRITE ANYTHING DOWN

Go over the picture once and count: how many boxes, how many arrows. Then make sure what you write
has that many. This is a check on yourself, and a dense diagram is exactly where a reading quietly
stops short.

{{vocabulary}}

MAKE IT JOIN UP

Before you answer, check your own graph: every outcome you named should lead to a state, and every
state other than the start should be reached by an outcome you named. Where an arrow leaves a box
and you cannot tell where it lands, still declare the outcome and put the missing destination in
`unresolved` — a declared branch with an unknown end is far more useful than a branch nobody
recorded.

Do not invent a step the diagram does not draw. If it shows four decision points, declare four,
not the seven an agent like this usually has.

ALSO RECORD WHAT THE DIAGRAM ESTABLISHES IN PROSE

Separately from the graph, note anything the image says that a reader of the documentation should
know, as observations under the key each most directly informs:

{{facets}}

THE SOURCE

Image file: {{filename}}

OUTPUT

Return ONLY a JSON object of the form:

{"capabilities": [{"id": "CAP-01", "name": "...", "type": ""}],
 "decisions": [{"id": "DEC-01", "name": "...", "outcomes": ["Pass", "Fail"], "capability_id": "CAP-01",
                "inputs": "", "input_source": "User", "max_attempts": 1, "outcome_condition": ""}],
 "states": [{"id": "S-00", "reached_via": "Start", "description": "...", "next_decisions": ["DEC-01"],
             "is_terminal": false, "outcome_type": ""}],
 "observations": [{"facet": "...", "statement": "...", "quote": "...", "locator": "top left"}],
 "unresolved": ["..."]}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
