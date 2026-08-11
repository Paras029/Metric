WHAT THIS IS FOR

An independent team is about to build a scenario space for an AI agent it did not build.
Everything the scenario space can test comes from what is understood about the agent here, so a fact
you miss is a fact nobody tests, and nobody finds out.

You have the complete documentation the model owner submitted, below. Read all of it and
answer the questions in this section.

WHAT WAS SUBMITTED

{{documents}}

Every one of these was sent because someone thought it bore on the agent. Read all of them, not
only the longest. The main document describes the intended design; the rest is where the
exceptions live — a vendor limitation, a policy extract that overrides the general rule, a
spreadsheet of thresholds, a deck that states what the prose only implies. A pack read as though
it were its largest file misses exactly the material that makes a scenario space worth having.

THE QUESTIONS

{{questions}}

HOW TO ANSWER THEM

The documents will not answer these in one place. They were written to describe a system to
people who already have the context, not to answer these questions, so what you need is spread
about: a process in one section, its exception three pages later, the threshold that governs it
in a table, the term that names it in a glossary.

Gather that scattered material and restructure it into an organised answer. You are expected to
combine what several passages say, resolve it into an ordered account, and put it in a form
someone can use who has never read the documents. That is the work.

Do not go beyond the documents. Where they do not settle something, record it under `unknowns`
rather than completing the picture with what such a system usually does — an invention is worse
here than an admitted gap, because the gap can be asked about and the invention is silently
tested as though it were real.

BE COMPREHENSIVE

Detail is the point. Include names, thresholds, conditions, sequences, exceptions, and the exact
vocabulary the documents use. Where the material describes a process, lay out its steps in order.
Where it describes branching, say what the branches are and what decides between them.

If two documents conflict, report both and say they conflict. Do not pick a winner.

When unsure whether to include a detail, include it. Length is not a virtue on its own, but
omission is a real cost.

CITING

Every answer carries `evidence`: short verbatim quotes copied from the documents, each with the
document name and the location marker that precedes it in square brackets. Copy them exactly —
they are checked against the source, and a rewritten quote will not be found. Three or four per
answer is enough; they are there so a reader can follow the answer back, not to reproduce the
document.

THE DOCUMENTS

{{corpus}}

WHAT TO RETURN

An object keyed by the question ids given above. For each:

- `answer`: a thorough prose account, organised for a reader who has not seen the documents.
  Several paragraphs where the material supports it.
- `points`: the same substance as itemised, self-contained statements — one fact, rule, step,
  branch or threshold each. This is what the intake is built from, so favour precision and
  granularity: a dozen exact points beats three broad ones.
- `unknowns`: at most three per question, and only where the missing fact would change which
  scenarios get written or how one of them is judged. Write each as the question you would put to
  the model owner who submitted the documents.
- `evidence`: list of `{"quote": "...", "document": "...", "locator": "..."}`.
- `confidence`: `High`, `Medium` or `Low` — how completely the documents answer the question.

WHAT COUNTS AS AN UNKNOWN

A high bar, because every one of these ends up in front of a person who has to chase it. Raise it
only where not knowing changes the testing:

- a branch whose outcomes are not stated, so the routes through it cannot be enumerated
- a threshold or limit that decides between two behaviours
- a rule the agent must honour where the documents say it exists but not what it says
- a hand-off to a person where the trigger is not stated

Do not raise a missing detail that would not change a scenario: an internal implementation choice,
a version number, who owns a system, wording the documents merely paraphrase. Where the documents
are simply thinner on a question than you would like, say so through `confidence` — that is what
it is for.

ACCOUNTING FOR THE TEMPLATE

Return `documents_used`: the names, exactly as listed above, of every document any part of your
answers rests on. Judge it honestly. A document listed there and not actually used is worse than
one honestly omitted, because the omission gets investigated and the false entry does not.

OUTPUT

Return ONLY a JSON object of the form:

{"documents_used": ["..."],
"question_id": {"answer": "...", "points": ["..."], "unknowns": ["..."],
"evidence": [{"quote": "...", "document": "...", "locator": "..."}], "confidence": "Medium"}}

Include every question id, even where the answer is empty and the unknowns say what is needed. No
markdown fences and no text outside the JSON. Keep each string value on a single line — separate
paragraphs with two spaces rather than line breaks.
