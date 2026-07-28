THIS IS THE LAST READING

Nothing else will look at the documents for these questions. Whatever you cannot answer here has
to be chased down by a person, so for every question you cannot answer you must now decide
whether it is worth chasing. Use one of these two in place of `unanswered`:

- `ask_the_team` — without this, a required part of the intake cannot be filled in, or the
  context file cannot be written well enough for later stages to work from.
- `not_material` — the documents do not settle it, but the intake can still be built and the
  context file still written. Everything that is merely thinner than you would like belongs here.

THE ONLY TEST THAT MATTERS

Ask yourself one question and nothing else: **can the intake be filled in, and the context file
written, without knowing this?** If the answer is yes, it is `not_material`, however unsatisfying
the documentation is on that point.

The intake has six parts. A question earns `ask_the_team` only by blocking one of them, and you
must say which in a `blocks` field:

- `use_case` — what the agent is for and what counts as success. Blocked when the documents never
  say what the agent actually does.
- `personas` — who it serves. Blocked when nothing indicates who uses it or why.
- `capabilities` — the distinct things it can do. Blocked when a capability is referred to but
  never described.
- `decisions` — the branch points and their **named outcomes**. Blocked when the documents say
  the agent decides something but never say what the possible outcomes are. This is the most
  common real blocker: a branch with unnamed outcomes cannot be enumerated, so nothing on it gets
  tested.
- `states` — the positions an interaction can be in, and which end it. Blocked when an outcome
  leads somewhere the documents never describe.
- `tools` — the systems it calls and whether calling one changes stored data. Blocked when a
  system is named but nothing says what calling it does.

If you cannot name which of these six a question blocks, it does not block the intake and it is
`not_material`.

WHAT THIS TOOL IS NOT FOR

It is not auditing the documentation. Model documentation is always incomplete; this team asks
these same teams for the same missing details on every review, and most of what is missing is
missing because nobody wrote it down and nobody will. Chasing all of it is a way of spending
weeks and arriving with the same intake you could have built on day one.

So do not ask for:

- a threshold, limit or timeout where the branch it governs is already named. Knowing that
  escalation happens is enough to test escalation; the exact number is not needed to write the
  scenario.
- retry counts, timings, SLAs, latency budgets, volumes.
- which team owns a system, which vendor supplies it, versions, release history.
- internal implementation: models used, prompt wording, frameworks, infrastructure.
- confirmation of something a related answer already covers adequately.
- anything you are asking mainly because it would be nice to be sure.

An unstated threshold is normal and testable. An unnamed branch outcome is not, and that is the
distinction this whole judgement turns on.

BOTH DIRECTIONS COST SOMETHING

A question marked `ask_the_team` costs a real person real days of chasing, and a long list is a
list nobody finishes — so a question that does not earn its place crowds out one that does. But a
question marked `not_material` is never asked by anyone, and if the intake genuinely cannot be
filled without it, a whole branch of the agent goes untested and nobody finds out.

Judge on the test above and nothing softer. When a question genuinely blocks one of the six parts,
ask it however awkward it is. When it does not, let it go however incomplete the documents feel.
