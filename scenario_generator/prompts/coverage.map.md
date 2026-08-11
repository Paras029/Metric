WHAT THIS IS

Below is a space of scenarios generated independently from the agent's own documentation, and
a set of conversations the model owner actually ran against the agent. Your job is to say, for
each conversation, which single scenario it demonstrates — or that none of them does.

This is how the validator finds out what the model owner's testing already covers and, more
importantly, what it does not.

THE AGENT

{{use_case}}

THE SCENARIO SPACE

Each of these is one complete route through the agent, from the opening of the conversation to a
specific ending. **A scenario is identified by where it ends as much as by what it does on the
way.**

{{scenarios}}

THE RULE THAT MATTERS MOST

A conversation maps to the scenario it *completely* represents — the one whose whole route it
walked, ending included. Scenarios are atomic. Some are, in effect, prefixes of longer ones, and
that is exactly where this goes wrong if you are not careful:

- A conversation that authenticates the caller and **stops there** maps to the scenario that ends
  at authentication. It does **not** map to a longer scenario that authenticates and then goes on
  to do something else, even though that longer scenario contains everything the conversation did.
- A conversation that authenticates and **then verifies a charge and ends there** maps to the
  charge-verification scenario. It does **not** map to the authentication-only scenario, even
  though it passed through authentication on the way.

So: match on the whole arc, and let the ending decide between candidates that share a beginning.
Where two scenarios both fit the route, the right one is the one whose ending is the conversation's
ending. Where the conversation ends somewhere no scenario ends, that is very likely a `no_match`
rather than a near-enough fit.

Exactly one scenario, or none. Never a list.

WHAT MAKES A MATCH CONFIDENT

- `high` — the route and the ending are both plain from the transcript, and one scenario fits them.
- `medium` — the ending is clear but part of the route is inferred, or two scenarios are close and
  one fits better.
- `low` — the transcript is short, ambiguous, or cut off, and this is the best available reading
  rather than a clear one.

Confidence is shown to a person who will decide what to do about it, so a `low` you are honest
about is far more useful than a `high` you are not.

WHEN NOTHING FITS

Return `"scenario_id": ""` with a reason. This is a genuine finding: it means either the model owner is
testing something the scenario space does not cover — worth knowing, and worth adding — or the
conversation is not really a test of this agent at all. Do not stretch to the nearest scenario to
avoid an empty answer.

THE CONVERSATIONS

Some carry a label the model owner filed them under. **Ignore it when deciding the match.** It
is shown only so you can see what the model owner called it; whether that grouping agrees with the
scenario space is assessed separately, and a wrong label must not pull the mapping toward it.

{{conversations}}

OUTPUT

Return ONLY a JSON object keyed by conversation id:

{"C1": {"scenario_id": "SC-004", "confidence": "high", "intent": "Dispute a charge after
identity is confirmed", "ending": "The dispute is filed and a reference is given",
"reason": "..."},
 "C2": {"scenario_id": "", "confidence": "high", "intent": "...", "ending": "...",
"reason": "No scenario ends with the call being transferred before identity is
established."}}

`intent` is what the user came to do, in a few words. `ending` is how the conversation actually
finished. Both are read back to a person, so write them as the plain sentences they are.

No markdown fences and no text outside the JSON. Keep every string value on a single line.
