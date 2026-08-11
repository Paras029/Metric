WHAT YOU ARE DOING

The intake below was drafted from the documentation, and a structural check has found specific
things missing from it. Each one is listed. Your job is to **fill in those specific gaps**, and
change nothing else.

These are not opinions about the declaration. Every point below is a property the graph must have
before it can be walked at all -- a branch with fewer than two named outcomes produces no routes,
an outcome that leads nowhere stops the route there, a state nothing reaches is never the ending
of any scenario. As it stands, the parts of the agent these concern will not be tested.

WHAT IS CURRENTLY DECLARED

{{current}}

WHAT IS MISSING

{{problems}}

WHAT THE DOCUMENTS ESTABLISH

Everything read out of the submitted documentation, plus anything the validator has added. The
answer to most of the points above is somewhere in here -- it was simply not carried through into
the declaration on the first pass.

{{context}}

WHAT THE DIAGRAMS SHOWED

{{structure}}

WHAT THIS DECLARATION CURRENTLY ENUMERATES TO

The arithmetic of the graph exactly as declared above, walked from every start state. This is not
a judgement about the agent — an agent that really does one thing should enumerate to one route,
and that is a correct answer. It is here so you can see the consequence of the wiring you are
looking at, because the routes are what the whole exercise tests and nothing else in this prompt
shows them.

{{enumeration}}

HOW TO FIX IT

Work through the listed points one at a time.

For each one, look for what the documentation actually says. A decision with one outcome usually
has its other outcomes named somewhere in the prose or drawn in a diagram -- a check that can pass
almost always has a named way to fail. An outcome that leads nowhere usually has its destination
described a paragraph later. A state nothing reaches is usually reached by an outcome that was
named on a different decision.

Where the documentation genuinely does not say, **still fill it in with what the structure
requires**, and say in a review note that you inferred it. A branch declared with one outcome is
untestable; the same branch with a named failure outcome is testable and can be corrected by a
person in ten seconds. Leaving it blank is the only option that helps nobody.

Outcomes are allowed to converge. Two outcomes of different decisions can both lead to the same
state, and a state can name several of them in its `reached_via`, comma-separated: `DEC-02=Too
old, DEC-05=Withdrawn`. A branch that rejoins the main flow is a normal shape and declaring it
that way is better than inventing a separate state that describes the same position twice.

Two things you must not do. Do not remove a row because it is the easier way to make a point go
away -- a decision deleted is a part of the agent nobody tests, and anything you drop is put back
before this is written, so removing a row only costs you the chance to correct it. And do not
change anything the list above does not concern: every other row is carried forward exactly as
it is.

WHAT TO RETURN

The complete intake, in the same shape as the declaration above -- every row, not only the ones
you changed.

- `use_case`: object with `name`, `objective`, `agent_type`, `channel`, `handoff_triggers`,
  `safety_requirements`, `success_criteria`.
- `personas`: list of `{"id", "name", "applies_to", "is_default"}`.
- `capabilities`: list of `{"id", "name", "type"}`.
- `decisions`: list of `{"id", "name", "capability_id", "inputs", "outcomes", "input_source",
  "max_attempts", "outcome_condition"}`.
- `states`: list of `{"id", "reached_via", "description", "next_decisions", "is_terminal",
  "outcome_type"}`.
- `tools`: list of `{"name", "capability_id", "is_state_changing"}`.
- `review_notes`: list of `{"field", "note"}` -- one for every point you filled in from inference
  rather than from something the documentation says, naming the row and what you assumed.

Return ONLY the JSON object. No markdown fences and no text outside it.
