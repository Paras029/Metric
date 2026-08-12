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

THE VOCABULARY TO WRITE IT IN

**Capabilities** — each distinct thing the agent can do. `CAP-01`, `CAP-02`, … Give each a type
where the diagram makes it plain: `Lookup` (reads and reports), `Transactional` (changes stored
state), `Gating` (decides whether something else may proceed — authentication is the usual one),
`Advisory` (recommends), `PII-handling` (touches personal data). Leave the type empty rather than
guessing; it decides which adversarial probes get applied.

**Decisions** — every branch point. `DEC-01`, `DEC-02`, … Each carries:
- `name`: the box's own label, in the diagram's words. If a box says "Auth check", write "Auth
  check", not "identity verification" — the rest of the documentation will use the diagram's own
  words, and a tidied label stops the two matching up.
- `outcomes`: the arrow labels leaving it, as written. **This is the most important field in the
  whole structure** — a branch whose outcomes are not named cannot be enumerated, so nothing on it
  is ever tested. Never invent an outcome name: a wrong one becomes a test of something that does
  not exist. An unlabelled arrow whose meaning you cannot read goes in `unresolved`.
- `capability_id`: which capability it belongs to, where the diagram groups them.
- `input_source`: `User`, `Tool`, `Memory-Session`, `Memory-CrossSession`, `System-Context` or
  `Document`. Only `User` steps become conversational turns, so this is what says whether a tester
  can drive the step or whether it happens inside the agent. Default to `User`.
- `max_attempts`: how many times it may be retried. A loop drawn back to the same box is a retry —
  if the diagram says how many, use that; if it shows a loop without a number, use 1 and note it
  in `unresolved`.
- `outcome_condition`: the rule or threshold selecting between outcomes, only where one is drawn.

**States** — every position the interaction can be in. `S-00`, `S-01`, … Each carries:
- `reached_via`: `Start` for the opening state, otherwise exactly `DEC-xx=Outcome`, naming the
  decision and the outcome that leads here. **This is what connects the graph.** Every outcome you
  declared on a decision should appear here on some state.
- `description`: what the box says, or what the position is.
- `next_decisions`: the decisions reachable from here. Empty where the interaction ends.
- `is_terminal`: whether the flow stops here — a hand-off to a person, a rejection, a completed
  outcome. **Do not mark a state terminal because its next step is unclear.** A box whose outgoing
  arrow you could not follow is a gap to name in `unresolved`, not an ending. The two look
  identical once written down, only one is true, and nothing later re-derives this from the
  picture.
- `outcome_type`, on terminal states only: `Happy path`, `Retry`, `Fallback`, `Escalation` or
  `Termination`.

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
