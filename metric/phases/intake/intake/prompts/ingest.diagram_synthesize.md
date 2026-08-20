WHAT THIS IS

Below are readings of {{document}} — one per image, each taken by looking at that image alone.
Together they are one workflow, split across images because it did not fit in a single picture.
Node references are prefixed with the image they came from, so `i2.n4` is node `n4` of image 2.

Your job has two parts, in this order:

1. **Join them into one graph.** An arrow recorded under `continues_offpage` in one image is the
   same arrow as whatever picks it up in another; follow it and connect the two. A box drawn in
   two images so the join makes sense is one box, not two.
2. **Write that graph down in the vocabulary the validator's intake uses**, which is what
   the rest of this exercise is built from.

THE READINGS

{{readings}}

{{vocabulary}}

Each image's own reading already classified every box's `kind`, and that classification
stands: a node read as `terminal` is terminal, a node read as `state` is not — even where
the arrow leaving it points at a box the readings did not fully resolve.

MAKE IT JOIN UP

Before you answer, check your own graph: every outcome you named should lead to a state, and
every state other than the start should be reached by an outcome you named. Where a reading said
an arrow ran off the page and nothing picks it up, still declare the outcome and put the missing
destination in `unresolved` — a declared branch with an unknown end is far more useful than a
branch nobody recorded.

Do not invent a step no reading described. If the diagrams show four decision points, declare
four, not the seven an agent like this usually has.

ALSO RECORD WHAT THE DIAGRAMS ESTABLISH IN PROSE

Separately from the graph, note anything the images say that a reader of the documentation should
know, as observations under the key each most directly informs:

{{facets}}

OUTPUT

Return ONLY a JSON object of the form:

{"capabilities": [{"id": "CAP-01", "name": "...", "type": ""}],
 "decisions": [{"id": "DEC-01", "name": "...", "outcomes": ["Pass", "Fail"], "capability_id": "CAP-01",
                "inputs": "", "input_source": "User", "max_attempts": 1, "outcome_condition": ""}],
 "states": [{"id": "S-00", "reached_via": "Start", "description": "...", "next_decisions": ["DEC-01"],
             "is_terminal": false, "outcome_type": ""}],
 "observations": [{"facet": "...", "statement": "...", "quote": "...", "locator": "image 2, top left"}],
 "unresolved": ["..."]}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
