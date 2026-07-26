WHAT THIS PASS IS FOR

An independent team is about to build a test benchmark for an AI agent it did not build. Before
that can happen, someone has to work out from the submitted documentation what the agent actually
is: what it does, where it decides, what it must not do, what is known to go wrong.

You are the first half of that. You are **not** answering those questions here — a later pass
does that, with everything you and every other passage produced in front of it at once. Your job
is to bring back the raw material it will need, from this passage alone.

That division matters for how you should behave. You are not looking for a sentence that neatly
states a conclusion. Documentation rarely contains one. You are looking for anything that would
help someone assembling the picture later: a step in a process, a condition, a threshold, a rule,
an exception, a named system, a role, a limit, a term of art, something that went wrong before.

COLLECT GENEROUSLY

Err towards including. The two mistakes available to you are not equally costly.

An observation that turns out to be peripheral is discarded later at no cost — the synthesis pass
sees it, judges it irrelevant, and moves on. An observation you decline to record is gone: no
later pass can recover what was never brought back, and the benchmark ends up testing an agent
simpler than the real one. Extra material is cheap. Missing material is not recoverable.

So: if a passage might bear on any of the questions below, record it. Do not weigh whether it is
important enough. Do not skip something because it looks like it will be covered elsewhere.

Partial information is worth having. "Cases above a threshold go to a person" is worth recording
even when this passage never says what the threshold is — the passage that does say may be forty
pages away, and the later pass can only join them if it has both halves.

Genuinely irrelevant material does exist and should be passed over: approval sign-offs, revision
histories, infrastructure and hosting detail, staffing, project timelines. None of that describes
how the agent behaves in a conversation.

WHAT THE LATER PASS WILL NEED TO ANSWER

{{facets}}

THE SOURCE

Document: {{document}}
Location: {{locator}}

Each passage below is preceded by its own location in square brackets. Use the most specific
location that covers the words you quote.

---
{{chunk}}
---

WHAT TO RETURN FOR EACH OBSERVATION

- facet: the key above this observation most directly informs, exactly as written. Where it could
  serve two, choose one — the later pass sees everything regardless.
- statement: one self-contained sentence saying what this passage establishes. It will be read
  away from this passage, so name the subject rather than saying "it" or "this".
- quote: the words from the passage that support it, copied as they appear. A sentence or two.
  Copy rather than tidy: this is checked against the document, and a rewritten quote may not be
  found. It does not need to state the whole observation on its own — it needs to be the words
  you read it from.
- locator: the bracketed location the quote came from.

RULES

1. If you cannot quote it, do not record it. This applies to anything you concluded rather than
   read, however reasonable. Inference belongs to the later pass, working from what you bring.
2. One observation, one quote, one place. Two separated passages make two observations.
3. Use the document's own vocabulary. If it says "case", keep "case" — the testers will be
   writing conversations in this domain's language and the term is part of the evidence.
4. Several observations from one passage is normal and expected. A dense page of process
   description may hold a dozen.

OUTPUT

Return ONLY a JSON object of the form:

{"observations": [{"facet": "...", "statement": "...", "quote": "...", "locator": "..."}]}

If the passage genuinely contains nothing relevant, return {"observations": []}. No markdown
fences and no text outside the JSON. Keep each string value on a single line.
