WHAT YOU ARE DOING

{{cds}}

A declaration has been drafted from the documentation and its structural gaps have already been
put back to the documents once. What has not happened is anybody standing back and reading the
whole thing as a description of one agent.

That is this pass. It is the last look before a person reviews it, and it is the only pass that
sees the declaration whole rather than a row or a gap at a time. Two drafting passes working
question by question reliably produce a declaration where every row is defensible and the flow as
a whole does not quite hang together: a decision that duplicates one three rows above under a
different name, a state everything routes through that nothing routes out of, an ending nobody
can reach, a retry limit on a decision that cannot repeat.

WHAT IS CURRENTLY DECLARED

{{current}}

WHAT THE DOCUMENTS ESTABLISH

{{context}}

WHAT THE DIAGRAMS SHOWED

{{structure}}

WHAT THIS DECLARATION CURRENTLY ENUMERATES TO

Every route through the graph exactly as declared. Read it as the answer to "what would a tester
actually be asked to run?" -- a route that reads as nonsense here is a wiring mistake above, and
this is usually where it becomes obvious.

{{enumeration}}

{{wiring}}

WHAT TO LOOK FOR

**1. Wiring that does not connect.** The most valuable thing this pass does. Take each decision
and ask how it is arrived at; take each state and ask what arrives at it. Anything that cannot be
answered is a row that has fallen out of the graph and is testing nothing. Reconnect it where the
declaration and the evidence make the connection plain.

**2. Routes that read as nonsense.** Walk the enumeration above. A route that verifies a customer
before identifying them, that ends in the middle of a check, that reaches a happy path without
passing the gate protecting it, or that loops without a bound is a wiring mistake somewhere
upstream. Find it and correct it rather than patching the symptom.

**3. Endings that are not endings, and endings that are missing.** A state marked terminal that
still names decisions after it, an intermediate state nothing leads out of, a terminal state with
no outcome type, or a decision whose failure outcome has no ending anywhere.

**4. Metadata that contradicts the flow.** `max_attempts` above 1 on a decision no state routes
back into -- the retry is declared but unreachable, so either the loop-back is missing or the
limit is wrong. `input_source` of `User` on a decision the agent plainly makes internally, or a
tool-driven source on a question the agent asks the customer. An outcome type of `Happy path` on
a rejection. A capability type that does not match what its decisions do.

**5. Rows that duplicate each other.** Two decisions that establish the same fact under different
names, two states describing the same position, a tool named after the capability it belongs to,
a persona that is a tone rather than an objective. Handle these two ways, and the difference
matters:
   - **Two states describing the same position** may be folded into one, with the surviving state
     naming every outcome that reached either of them in its `reached_via`. This is safe because
     nothing is lost -- every route still arrives somewhere.
   - **Two decisions that look redundant must not be merged here.** Merging branch points changes
     what gets tested, and getting it wrong silently deletes coverage. Leave both in place and
     say so in a review note naming both ids, so a person can make that call.

**6. Whether the use case still describes this graph.** The objective, the hand-off triggers and
the success criteria were written early. Do they match the agent the graph now describes? A
hand-off trigger with no escalation state, or an escalation state no stated trigger accounts for,
is worth a review note either way.

WHAT NOT TO DO

**Do not remove decisions, capabilities, personas or tools.** Anything you drop is put back before
this is written, so removing a row costs you the chance to correct it and gains nothing. A row
you believe should not be there goes in the review notes.

**Do not add an agent nobody described.** This pass is a reconciliation, not a second draft. A
new decision is justified only where the routes as declared cannot be walked without it and the
documentation supports it. Tidiness is not a reason.

**Do not rewrite wording that is already right.** Descriptions a person may have corrected by
hand, outcome names taken from the documentation, ids -- all carried forward exactly. Change what
is wrong, and nothing else.

WHAT TO RETURN

The complete declaration, every row, in the same shape as above:

- `use_case`: object with `name`, `objective`, `agent_type`, `channel`, `handoff_triggers`,
  `safety_requirements`, `success_criteria`.
- `personas`: list of `{"id", "name", "applies_to", "is_default"}`.
- `capabilities`: list of `{"id", "name", "type"}`.
- `decisions`: list of `{"id", "name", "capability_id", "inputs", "outcomes", "input_source",
  "max_attempts", "outcome_condition"}`.
- `states`: list of `{"id", "reached_via", "description", "next_decisions", "is_terminal",
  "outcome_type"}`.
- `tools`: list of `{"name", "capability_id", "is_state_changing"}`.
- `review_notes`: list of `{"field", "note"}` -- one for every change you made and why, and one
  for every problem you found and deliberately left for a person. Name the ids. This is the part
  of the output a reviewer reads first.

Return ONLY the JSON object. No markdown fences and no text outside it.
