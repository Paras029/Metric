{{owner}}THE SCENARIO SPACE AS IT STANDS ({{total}} scenarios)

Every scenario currently in the scenario space, in compact form, so each can be judged against the
whole rather than on its own. Use it to see redundancy, imbalance, and coverage that clusters in
one area while leaving another thin.

{{digest}}

YOUR TASK FOR THIS BATCH

Settle the materiality of each scenario below, decide whether anything is wrong with it, and where
something is, fix it.

Each scenario carries `what_each_turn_must_induce`: what the walk through the declared graph says
happens at each turn — the decision reached, the outcome it must land on, and who drives it. That
is ground truth. The description and the turn plan are prose somebody wrote to make it happen, and
they are the part that can be wrong. Check one against the other.

{{batch}}

THE THREE QUESTIONS, IN ORDER

**1. What does failure cost?** What actually happens to the business and to the user if the agent
handles this badly? Answer in concrete terms before reaching for a tier — the tier follows the
consequence, not the other way round.

**2. Does the turn plan actually produce this route?** This is a check against
`what_each_turn_must_induce`, not a judgement of the writing. Go turn by turn:

- Is there a turn in the plan for every turn the route needs? A plan with four turns against a
  six-step route is missing two, and a tester following it stops short of what the scenario is
  for.
- Does each turn do the thing that lands on the outcome named for it? A route says turn 3 must
  land on "Declined". A plan whose turn 3 is "provide the account details" does not say which
  details, so it does not say declined — it says either. The plan has to make the outcome the
  route needs the one that happens.
- Where the same decision is reached more than once, does the plan distinguish the attempts? Two
  turns that read identically against a route that expects a failure and then a success is one
  turn written twice.
- Does the description match where the route actually ends?
- Could a tester who has never seen this agent run it and get the same result as another tester
  running it? Read it as that person: no documentation, no one to ask. "Provide the relevant
  details", "continue the conversation", a turn that names the step instead of the tester's part
  in it — each of those is two testers running two different tests, and transcripts that cannot
  be compared.

**3. Does it test something the rest of the scenario space does not cover better?** The digest is
what you check that against. Two scenarios walking the same block are not duplicates because they
walk the same block — check `tests_capability` and `already_established` first, because a block
entered two different ways is two different tests. Duplication is the same route, from the same
starting position, to the same ending.

WHEN SOMETHING IS WRONG, FIX IT

A flag on its own is advice nobody acts on. Text you call under-specified is issued to the model
owner exactly as it stands — nothing after you reads it again. So where you flag a scenario,
return the corrected text with it.

Write the repair from `what_each_turn_must_induce` and the rest of the scenario's metadata. You
are not inventing a new scenario: the route is fixed, the block it tests is fixed, the position it
starts from is fixed. You are writing the prose that makes that route happen, in the terms it
should have been written in the first time — naming the value, the condition, the tester's actual
words or action at each turn.

A repair has to stay runnable by conversation alone. A tester can say things to the agent and
supply details; they cannot change what is in a system, force a tool to fail, or reach behind the
agent. If the only way to induce the route is something a tester cannot do by talking, say that in
the rationale and leave the text alone.

Leave `revised_description` and `revised_turn_plan` empty where the existing text is fine. Repair
what you flagged; do not restyle what you did not.

WHAT TO RETURN FOR EACH SCENARIO

- materiality: one of {{materiality}}. Reach it from the evidence, then compare with the tier
  already recorded. Where you land in the same place, repeat it — agreement is the expected
  outcome for most scenarios and needs no justification beyond the rationale.
- rationale: one or two sentences giving the business consequence of failure in concrete terms,
  and what in the wider scenario space supported the tier. Where redundancy drove it down or
  absent coverage drove it up, name the scenarios involved. Where you flagged it, say what was
  wrong in terms somebody can check: "turn 2 says to give the account details without saying which
  ones make it decline" is something a person can verify against the route; "the wording is
  unclear" is not.
- flag: "" for nothing to raise, or one of:
  - "Redundant" — the same route from the same starting position to the same ending as another
    scenario. Name it.
  - "Under-specified" — the plan does not produce the route, or two testers would run it
    differently.
  - "Mis-scoped" — the description does not match what the route or the expected outcome implies.
- revised_description: the corrected description, or "".
- revised_turn_plan: the corrected plan as numbered lines, "1. ", "2. ", joined with the two
  characters \n, or "". One line per turn the route needs. Only what the tester says or does.

Most scenarios should come back with "" for the flag and both revisions. A flag is a
recommendation to a human reviewer; nothing is removed automatically, so flag what genuinely
warrants a second look rather than everything that could conceivably be improved.

"Under-specified" is the exception. It is not a matter of taste and it is not a high bar: the plan
either produces the route or it does not, and you can see the route. Where it does not, say so and
fix it, however many of them there are.

THE NON-DISCLOSURE RULE APPLIES TO ANYTHING YOU WRITE

description and turn_plan are issued to the model owner. They must never state or imply what a
correct agent response looks like — that is what is being tested. Anything of the form "the agent
should..." belongs nowhere in a revision.

OUTPUT

Return ONLY a JSON object mapping each scenario "id" to its object, of the form:

{"SC-001": {"materiality": "...", "rationale": "...", "flag": "", "revised_description": "",
"revised_turn_plan": ""}, "SC-002": {...}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
