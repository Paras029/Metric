{{owner}}THE BENCHMARK AS IT STANDS ({{total}} scenarios)

Every scenario currently in the benchmark, in compact form, so each can be judged against the
whole rather than on its own. Use it to see redundancy, imbalance, and coverage that clusters in
one area while leaving another thin.

{{digest}}

YOUR TASK FOR THIS BATCH

Settle every judged column on each scenario below, in one reading. There are three things to
settle and they are asked together because they are answered from the same material: you have the
route, the expected outcome and the description in front of you, and reading them once to answer
all three is both cheaper and more coherent than reading them three times.

**1. What is failure here worth?** Work through it in this order, because the tier follows the
consequence rather than the other way round:

- What actually happens to the business and to the user if the agent handles this badly? Answer
  in concrete terms first.
- Does the description match what the route or the expectation implies, and could a tester who
  has never seen this agent run it and get a consistent result?
- Does it test something the rest of the benchmark does not already cover better? The digest
  above is what you check that against.

**2. Is it filed under the right ending?** A category is not a label on the scenario, it is a
statement about **how the interaction ends**. It is set from the Outcome Type the model owner
declared on the state the route finishes in, so a wrong category almost always means that
declaration is wrong, or was left blank and guessed at from the wording of an outcome. That is
worth catching: it is a defect in how the agent was described to the validator, and it is
invisible anywhere else.

- **Happy path** — the interaction reaches what the user came for. The agent did the thing.
- **Retry** — the interaction goes round again after something did not succeed first time. The
  distinguishing feature is a second attempt at the same step, not that something went wrong.
- **Fallback** — the agent cannot do the thing, and handles that itself: a degraded answer, a
  narrower answer, a referral to self-service. Nobody is handed a person.
- **Escalation** — the agent hands the interaction to a human being. This is the one that most
  often gets mislabelled as Fallback; the test is whether a person picks it up.
- **Termination** — the interaction stops without the user getting what they came for and without
  a person taking over. A lockout, a refusal, an abandoned session.

Judge from the route and the expected outcome, not from the description's tone. A scenario can be
distressing to read and still be a Happy path; a scenario can read calmly and still end in a
lockout. Agreeing with what is recorded is the expected result — change a category when the route
plainly ends somewhere other than the recorded one says, not to express a preference between two
defensible readings.

**A probe has no category.** Probes are not routes through the graph and have no ending to
categorise; what they carry in that field is the probe family they came from. Return an empty
category for anything whose origin is "probe".

**3. Is anything wrong with it?** Most scenarios have nothing to raise. A flag is a recommendation
to a human reviewer and nothing is removed automatically, so flag what genuinely warrants a second
look rather than everything that could conceivably be improved.

{{batch}}

WHAT TO RETURN FOR EACH SCENARIO

- `materiality`: one of {{materiality}}. Reach it from the evidence, then compare with the tier
  already recorded. Where you land in the same place, repeat it — agreement is the expected
  outcome for most scenarios and needs no justification beyond the rationale.
- `rationale`: one or two sentences giving the business consequence of failure in concrete terms,
  and what in the wider benchmark supported the tier. Where redundancy drove it down or absent
  coverage drove it up, name the scenarios involved. Do not restate the tier definition back; a
  rationale that would fit any scenario at this tier is not a rationale.
- `category`: one of {{categories}}, or "" for a probe. Return the category you judge correct,
  which for most scenarios is the one already recorded.
- `category_rationale`: **only where you are changing the category.** One sentence saying what the
  interaction actually does at its end, and which declaration looks wrong as a result. Empty
  otherwise.
- `flag`: "" for nothing to raise, or one of:
  - "Redundant" — materially duplicated by another scenario. Name the duplicate in the rationale.
  - "Under-specified" — too vague for a tester to run consistently.
  - "Mis-scoped" — the description does not match what the route or expectation implies.

OUTPUT

Return ONLY a JSON object mapping each scenario "id" to its object, of the form:

{"SC-001": {"materiality": "...", "rationale": "...", "category": "...",
"category_rationale": "", "flag": ""}, "SC-002": {...}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
