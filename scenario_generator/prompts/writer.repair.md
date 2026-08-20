{{use_case}}
{{context}}

SCENARIOS TO WRITE AGAIN

You wrote each of these already and something specific is wrong with it. What is wrong is listed
against each one. Everything not listed is fine — change each as little as you can while fixing
what is named for it.

They are independent. A fault named against one says nothing about any other, and a rewrite that
carries a phrase from its neighbour has replaced one problem with a worse one.

Each entry carries:

- `id`: return your rewrite under this key.
- `wrote`: the three fields as they stand.
- `wrong`: what has to be fixed. This list, and nothing else.
- `scenario`: the route it is written from, in the same form it was written from the first time.

{{scenarios}}

{{house_style}}

WHAT TO RETURN

For every id in the list, the same three fields, corrected.

- `name`: a handle, six words or fewer, no ending full stop. The situation, never the expected
  behaviour.
- `description`: two or three sentences saying what situation the tester is setting up and what
  makes this route distinct. Name the real subject matter.
- `turn_plan`: as many numbered lines as this scenario's `turns_to_write`, written "1. ", "2. "
  and so on, joined with the two characters \n and nothing else. Each line tells the tester what
  to say or do.

**If what is wrong is that the text cannot be acted on**, the standard is this: the person running
it has never seen the agent, cannot read its documentation, and cannot ask you a question. A line
must name what the tester *supplies*, not that they supply something -- "give the booking reference
and the departure date", never "provide the relevant details". Where the route turns on a property
of what is supplied (a reference not on the account, an amount over a limit), name that property.
Leave no bracket for anybody to fill in.

**If what is wrong is that you stated how the interaction ends**, this is the thing to understand:
what you write here is issued to the model owner as the test they are asked to run. They must not
be able to read the answer off it. You have been given the outcome of each step on the route
because the tester has to know which condition to induce — you have *not* been given where the
route finishes, and anything you inferred about the finish belongs nowhere in these three fields.
Write the setup. The transcript records what happened.

OUTPUT

Work through them one at a time and return a separate, independent object for every "id" in the
list. Return ONLY a single JSON object mapping each "id" to its object, of the form:

{"SC-001": {"name": "...", "description": "...", "turn_plan": "1. ...\n2. ..."},
"SC-002": {...}}

No markdown fences and no text outside the JSON. Keep description on a single line, and use \n in
turn_plan only between numbered lines.
