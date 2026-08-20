{{use_case}}
{{context}}

ONE SCENARIO TO WRITE AGAIN

You wrote this scenario already and something specific is wrong with it. What is wrong is listed
below. Everything not listed is fine — change it as little as you can while fixing what is named.

WHAT YOU WROTE

{{current}}

WHAT IS WRONG WITH IT

{{problems}}

THE SCENARIO

{{scenario}}

{{house_style}}

WHAT TO RETURN

The same three fields, corrected.

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

Return ONLY a JSON object of the form:

{"name": "...", "description": "...", "turn_plan": "1. ...\n2. ..."}

No markdown fences and no text outside the JSON.
