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
- `description`: what situation the tester is setting up and what makes this route distinct,
  named in the real subject matter of this service. There is no length to hit. What it has to be
  is *apt*: somebody who has never seen this agent reads it and knows what they are being asked to
  bring about.
- `turn_plan`: as many numbered lines as this scenario's `turns_to_write`, written "1. ", "2. "
  and so on, joined with the two characters \n and nothing else. Each line tells the tester what
  to say or do.

THE RESULT HAS TO HOLD TOGETHER

"Change as little as you can" is about **scope** — do not go re-deciding what a scenario is about.
It is not a licence to leave a broken thing broken around the patch. Whatever you return is read
on its own by somebody with nothing else in front of them, so:

- **The turn plan is one conversation, in order.** Each line follows from the one before it. If
  the fault is that a line is missing, the fix is not a line bolted on the end — it is the turn
  that actually belongs at that point in the route, with the lines around it still reading into
  and out of it. A plan padded to the right length that no longer describes a conversation is
  worse than the short one it replaced.
- **The description has to be true of the route.** It is the reader's only account of what they
  are doing and why this run differs from the ordinary one. Vague is a fault; so is confident and
  wrong.
- **Where the declaration is silent, say what is known and stop.** A workflow with a gap in it is
  the normal case. Write the part that is stated, plainly, and leave the rest out — do not invent
  a detail to fill the space, and do not hedge the whole description into saying nothing.

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
