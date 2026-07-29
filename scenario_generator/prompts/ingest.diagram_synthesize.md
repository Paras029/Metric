WHAT THIS IS

Below are readings of {{document}} — one per image, each taken by looking at that image alone.
Together they are one workflow, split across images because it did not fit in a single picture.
Node references are prefixed with the image they came from, so `i2.n4` is node `n4` of image 2.

Your job has two parts, in this order:

1. **Join them into one graph.** An arrow recorded under `continues_offpage` in one image is the
   same arrow as whatever picks it up in another; follow it and connect the two. A box drawn in
   two images so the join makes sense is one box, not two.
2. **Write that graph down in the vocabulary the validation team's intake uses**, which is what
   the rest of this exercise is built from.

THE READINGS

{{readings}}

THE VOCABULARY TO WRITE IT IN

**Capabilities** — each distinct thing the agent can do. `CAP-01`, `CAP-02`, … Give each a type
where the diagram makes it plain: `Lookup` (reads and reports), `Transactional` (changes stored
state), `Gating` (decides whether something else may proceed — authentication is the usual one),
`Advisory` (recommends), `PII-handling` (touches personal data). Leave the type empty rather than
guessing; it decides which adversarial probes get applied.

**Decisions** — every branch point. `DEC-01`, `DEC-02`, … Each carries:
- `name`: the box's own label.
- `outcomes`: the arrow labels leaving it, as written. **This is the most important field in the
  whole structure** — a branch whose outcomes are not named cannot be enumerated, so nothing on it
  is ever tested.
- `capability_id`: which capability it belongs to, where the diagram groups them.
- `input_source`: `User`, `Tool`, `Memory-Session`, `Memory-CrossSession`, `System-Context` or
  `Document`. Only `User` steps become conversational turns, so this is what says whether a
  tester can drive the step or whether it happens inside the agent. Default to `User`.
- `max_attempts`: how many times it may be retried. A loop drawn back to the same box is a retry
  — if the diagram says how many, use that; if it shows a loop without a number, use 1 and note
  it in `unresolved`.
- `outcome_condition`: the rule or threshold selecting between outcomes, only where one is drawn.

**States** — every position the interaction can be in. `S-00`, `S-01`, … Each carries:
- `reached_via`: `Start` for the opening state, otherwise exactly `DEC-xx=Outcome`, naming the
  decision and the outcome that leads here. **This is what connects the graph.** Every outcome you
  declared on a decision should appear here on some state.
- `description`: what the box says, or what the position is.
- `next_decisions`: the decisions reachable from here. Empty where the interaction ends.
- `is_terminal`: whether the flow stops here.
- `outcome_type`, on terminal states only: `Happy path`, `Retry`, `Fallback`, `Escalation` or
  `Termination`.

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
