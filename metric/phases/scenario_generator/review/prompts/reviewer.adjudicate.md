TWO READINGS THAT DO NOT AGREE

Each scenario below was weighed twice and the two answers came out two or more tiers apart. That
is not a rounding difference. One of the two readings is missing something, and which one it is
matters: the tier decides how many runs the model owner is asked for, so a scenario at the wrong
tier is either a request nobody needed to make or a risk nobody tested.

You are not being asked to split the difference. Read what each said, decide which is right, and
say so. Landing on one of the two is the ordinary outcome; a third tier is available where both
readings missed the same thing.

THE SCALE

{{materiality}}

THE SCENARIO SPACE AS IT STANDS ({{total}} scenarios)

{{digest}}

THE DISAGREEMENTS

{{batch}}

WHAT TO RETURN FOR EACH SCENARIO

- `materiality`: the tier that is right. One of {{scale_values}}.
- `rationale`: one or two sentences. Say what the reading you are setting aside missed —
  a consequence it did not follow through, or a redundancy it did not see. A rationale that only
  restates the tier's definition is not one.

The two readings differ in what they were given, and that is usually where the answer is. The
first weighed the scenario against its immediate peers with the redundancy signals in hand. The
second weighed it against the whole scenario space. Neither is automatically right: the first sees the
detail of the route, the second sees whether anything else already covers it.

OUTPUT

Return ONLY a JSON object mapping each scenario "id" to its object, of the form:

{"SC-001": {"materiality": "...", "rationale": "..."}, "SC-002": {...}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
