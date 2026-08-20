{{owner}}THE SCENARIO SPACE AS IT STANDS ({{total}} scenarios)

Every scenario currently in the scenario space, in compact form, so each can be judged against the
whole rather than on its own. Use it to see redundancy, imbalance, and coverage that clusters in
one area while leaving another thin.

{{digest}}

YOUR TASK FOR THIS BATCH

Settle the materiality of each scenario below, and raise anything wrong with it.

For each one, work through three questions in order:

1. What actually happens to the business and to the user if the agent handles this badly? Answer
   in concrete terms before reaching for a tier -- the tier follows the consequence, not the
   other way round.
2. Could a tester who has never seen this agent run it and get the same result as another
   tester running it? Read the description and the turn plan as that person: they have no access
   to the documentation and cannot ask a question. A plan that says to "provide the relevant
   details" or "continue the conversation", a description that names no condition beyond the bare
   action, a turn that names the step instead of the tester's part in it -- each of those is two
   testers running two different tests, and transcripts that cannot be compared. Does the
   description also match what the route implies?
3. Does it test something the rest of the scenario space does not already cover better? The digest
   above is what you check that against.

{{batch}}

WHAT TO RETURN FOR EACH SCENARIO

- materiality: one of {{materiality}}. Reach it from the evidence, then compare with the tier
  already recorded. Where you land in the same place, repeat it -- agreement is the expected
  outcome for most scenarios and needs no justification beyond the rationale.
- rationale: one or two sentences giving the business consequence of failure in concrete terms,
  and what in the wider scenario space supported the tier. Where redundancy drove it down or absent
  coverage drove it up, name the scenarios involved.
- flag: "" for nothing to raise, or one of:
  - "Redundant" -- materially duplicated by another scenario. Name the duplicate in the rationale.
  - "Under-specified" -- a tester could not run it, or two testers would run it differently.
    Say in the rationale **what is missing**, in the terms it should have been written in: which
    value is not named, which turn has no instruction against it, which condition the description
    leaves out. "Turn 2 says to give the account details without saying which" is something
    somebody can fix; "the wording is unclear" is not.
  - "Mis-scoped" -- the description does not match what the route or expectation implies.

Most scenarios should come back with "". A flag is a recommendation to a human reviewer; nothing
is removed automatically, so flag what genuinely warrants a second look rather than everything
that could conceivably be improved.

"Under-specified" is the exception to "most should come back with nothing". It is not a matter of
taste and it is not a high bar to clear: a scenario either tells a tester enough to run it or it
does not. Where it does not, say so, however many of them there are -- text that cannot be run is
issued to the model owner exactly as it stands, and nothing downstream of you looks at it again.

OUTPUT

Return ONLY a JSON object mapping each scenario "id" to its object, of the form:

{"SC-001": {"materiality": "...", "rationale": "...", "flag": ""}, "SC-002": {...}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
