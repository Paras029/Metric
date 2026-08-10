{{owner}}THE BENCHMARK AS IT STANDS ({{total}} scenarios)

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
2. Does the description match what the route or the expectation implies, and could a tester who
   has never seen this agent run it and get a consistent result?
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
  - "Under-specified" -- too vague for a tester to run consistently.
  - "Mis-scoped" -- the description does not match what the route or expectation implies.

Most scenarios should come back with "". A flag is a recommendation to a human reviewer; nothing
is removed automatically, so flag what genuinely warrants a second look rather than everything
that could conceivably be improved.

OUTPUT

Return ONLY a JSON object mapping each scenario "id" to its object, of the form:

{"SC-001": {"materiality": "...", "rationale": "...", "flag": ""}, "SC-002": {...}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
