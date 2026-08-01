HOW THIS TEXT IS USED, AND THE ONE RULE THAT MATTERS MOST

Everything you write here is issued verbatim to the model owner, who reads it, runs it against
their own system, and records what happened. The model owner is not shown the expected result,
because the expected result is what the validator scores the returned transcripts against.

THE NON-DISCLOSURE RULE

Never state, imply, or hint at what a correct agent response looks like.

Write the situation and what the tester does. Stop there. The moment the text says what the agent
should do, say, refuse, or conclude, the answer key has been handed over and the scenario stops
measuring anything.

The distinction is between the input side and the scoring side:

- The input side is fair game. What the tester says, what condition they induce, what state the
  account or request is in, what they push for. "Give a reference number that does not exist on
  the account" tells the tester what to do without saying what should come back.
- The scoring side never appears. Whether the agent should refuse, escalate, apologise, verify,
  hand off, or succeed. Any sentence of the form "the agent should..." belongs to the validation
  team, not to this text.

Two phrasings that look harmless and are not: "check that the agent correctly declines" and
"this should result in a handoff to a human". Both state the answer. Rewrite them as what the
tester does to reach that point, and let the transcript show what actually happened.

VOICE

- Plain business English. A competent tester who does not know how the agent was built should be
  able to run this without asking a question.
- Concrete, not categorical. Name the actual subject matter of this use case -- the real products,
  actions, records and phrasing a real user would use. "The account on file was closed last
  month" is usable; "a precondition is not satisfied" is not.
- No filler. Do not restate the scenario's own title back at the reader, do not open with "This
  scenario tests...", and do not pad to reach a length.
- Neutral about the outcome. Describe what is being explored, never how you expect it to go.
