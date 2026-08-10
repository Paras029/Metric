{{owner}}THE BENCHMARK AS IT STANDS ({{total}} scenarios)

Every scenario currently in the benchmark, in compact form.

{{digest}}

YOUR TASK FOR THIS BATCH

Check that each scenario below is filed under the right category, and say so where it is not.

A category is not a label on the scenario, it is a statement about **how the interaction ends**.
It is set from the Outcome Type the model owner declared on the state the route finishes in,
which means a wrong category almost always means that declaration is wrong or was left blank and
guessed at from the wording of an outcome. That is worth catching: it is a defect in how the agent
was described to the validator, and it is invisible anywhere else.

The categories, and what each one actually means:

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
lockout.

{{batch}}

WHAT TO RETURN FOR EACH SCENARIO

- `category`: one of {{categories}}. Return the category you judge to be correct. Where that is
  the one already recorded — which it will be for most scenarios — return it unchanged.
- `rationale`: **only where you are changing it.** One sentence saying what the interaction
  actually does at its end, and which declaration looks wrong as a result. Empty otherwise.

Agreeing is the expected result. Change a category when the route plainly ends somewhere other
than the recorded category says, not to express a preference between two defensible readings.

OUTPUT

Return ONLY a JSON object mapping each scenario "id" to its object, of the form:

{"SC-001": {"category": "...", "rationale": ""}, "SC-002": {...}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
