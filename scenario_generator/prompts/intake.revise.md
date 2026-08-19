WHAT YOU ARE DOING

{{cds}}

The intake below is already declared -- drafted earlier, corrected by hand, or some of both. Since
it was last written, the validator has answered specific questions about it and possibly
added documents. Your job is to **revise it**, not redraft it from nothing: carry forward
everything that is already right, and change only what the new information below actually
requires.

This is the difference that matters most. A person may have corrected this declaration by hand
since it was drafted, in ways nothing in the evidence would tell you to change back. Treat every
part of the current declaration as correct unless the new context or a specific answer
contradicts it. When in doubt, keep what is there.

WHAT IS CURRENTLY DECLARED

{{current}}

WHAT HAS BEEN ADDED SINCE

Everything the documents establish, plus every answer the validator has given -- including
answers to specific questions raised about the declaration above.

{{context}}

WHAT THE DIAGRAMS SHOWED

{{structure}}

WHAT THE INTAKE IS

The agent described as a decision graph. Six parts, and they interlock -- the ids you use in one
must exist in another. Keep every id from the current declaration unless an answer explicitly
renames or removes something; a decision or state that gains a new id when nothing asked for one
breaks every reference to the old one.

**Use case.** Its name, the business objective in one or two sentences, what kind of agent it is,
the channel it runs on, what makes it hand over to a person, the rules it must honour, and what
counts as success.

**Personas.** A persona is a person arriving with an objective. A cooperative user and an
adversarial user are both expected to already be present; do not remove either. Add or sharpen a
persona only where an answer below says what the agent does differently for them.

**Capabilities.** Each distinct thing the agent can do, with an id, a name, and a type from
exactly: `Lookup`, `Transactional`, `Gating`, `Advisory`, `PII-handling`. Fill a blank type where
an answer settles it; do not change a type nothing below touches.

**Decisions.** Every point where the agent branches, with an id, the capability it belongs to,
and its named outcomes -- the words the documentation and the answers use, not a tidied paraphrase
of them. Each also carries `input_source`, `max_attempts`, and `outcome_condition`. Add an outcome
or correct a destination only where an answer or new evidence actually settles it; a decision
already declared correctly should come back unchanged.

**States.** Every position the interaction can occupy, with an id, `reached_via` (`Start`, or the
exact `DEC-xx=Outcome` that leads here), `next_decisions`, `is_terminal`, and, on a terminal state,
`outcome_type`.

**Tools.** The systems the agent calls, which capability each belongs to, and whether calling it
changes stored data.

{{wiring}}

HOW TO REVISE IT

Go through the current declaration row by row. For each one: does anything below change it? If
not, carry it forward exactly as it is, including any wording, phrasing or structure a person may
have corrected by hand. If something below does bear on it -- a specific answer naming that
decision or state, a document that settles what was previously unknown -- apply the change and
nothing more than the change.

Where an answer below is about something not yet in the declaration at all (a persona nobody had
named, a decision the diagrams did not show), add it as its own new row, with the next free id in
that part's own numbering.

Do not invent anything the evidence and the answers below do not support, and do not remove a row
because you cannot see what it is for -- removing something a person may be relying on elsewhere
is a worse mistake than leaving a weak row in place for them to reconsider.

One thing to correct even where nothing below asks for it: **wiring that does not connect**. A
decision no state offers, or a state whose `reached_via` names an outcome that does not exist, is
not a matter of opinion the current declaration is entitled to -- it is a row that has fallen out
of the graph and is testing nothing. Reconnect it where the declaration and the evidence make the
connection plain, and flag it in the review notes where they do not.

WHAT TO RETURN

- `use_case`: object with `name`, `objective`, `agent_type`, `channel`, `handoff_triggers`,
  `safety_requirements`, `success_criteria`. Carry forward anything not being changed.
- `personas`: list of `{"id", "name", "applies_to", "is_default"}`.
- `capabilities`: list of `{"id", "name", "type"}`.
- `decisions`: list of `{"id", "name", "capability_id", "inputs", "outcomes", "input_source",
  "max_attempts", "outcome_condition"}` where `outcomes` is a list of strings.
- `states`: list of `{"id", "reached_via", "description", "next_decisions", "is_terminal",
  "outcome_type"}` where `next_decisions` is a list of decision ids.
- `tools`: list of `{"name", "capability_id", "changes_state"}`.
- `confidence`: an object mapping each of `use_case`, `personas`, `capabilities`, `decisions`,
  `states`, `tools` to `High`, `Medium` or `Low` -- how well the evidence and the answers below
  now support that part, not how it stood before.
- `review_notes`: list of `{"field", "note"}`. What is still weak after this revision -- what an
  answer only partly settled, what remains inferred rather than stated. Carry forward a note that
  nothing below addressed; drop one that this revision has now settled.

OUTPUT

Return the **complete revised intake**, in the same shape as `WHAT IS CURRENTLY DECLARED` -- not a
list of changes, and not only the parts that changed. A row you leave out is a row you are saying
should be removed. No markdown fences and no text outside the JSON. Keep every string value on a
single line.
