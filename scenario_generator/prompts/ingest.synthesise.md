WHAT THIS PASS IS FOR

An independent team is building a test benchmark for an AI agent it did not build. Everything the
benchmark can test comes from what is understood about the agent here, so this answer is the
foundation the whole exercise stands on. A detail lost at this point is not tested later, and
nobody finds out.

An earlier pass read every submitted document and brought back individual observations, each
tied to the page it came from. You are answering **one question** from all of them at once.

THE QUESTION

**{{heading}}**

{{question}}

WHY THIS CANNOT BE COPIED OUT

The documents will not answer this in one place. They were written to describe a system to people
who already have the context, not to answer this question, so what you need is spread across
pages: a process described in one section, its exception three pages later, the threshold that
governs it in a table, the term that names it in a glossary.

Your job is to gather that scattered material and restructure it into a single organised answer.
That is a different act from quoting, and it is the reason this pass exists. You are expected to
combine observations, resolve what they say into an ordered account, and put it in a form someone
can use who has never read the documents.

You are not, however, free to go beyond them. Everything you write must rest on the observations
below. Where they do not settle something, say so under `unknowns` rather than completing the
picture with what such a system usually does — a plausible invention is worse here than an
acknowledged gap, because it will be tested as though it were real.

BE COMPREHENSIVE

Detail is the point. Include specifics: names, thresholds, conditions, sequences, exceptions,
the exact vocabulary the documents use. Where observations describe a process, lay out its steps
in order. Where they describe branching, say what the branches are and what decides between them.

If two observations conflict, report both and say they conflict. Do not pick a winner.

If observations vary in confidence — one states a rule outright, another only implies it — say
which is which in the answer rather than flattening them together.

Length is not a virtue in itself, but omission is a real cost. When unsure whether to include a
detail, include it.

THE OBSERVATIONS

Each carries an id. Cite the ids your answer rests on.

{{observations}}

WHAT TO RETURN

- answer: a thorough prose account, organised for a reader who has not seen the documents. Several
  paragraphs where the material supports it. This is the part that gets read.
- points: the same substance as an itemised list of specific, self-contained statements — one
  fact, rule, step, branch or threshold each. This is the part that gets turned into a structured
  intake, so favour precision and granularity: a dozen exact points beats three broad ones.
- unknowns: specific things this question needs and the documents did not settle. Write each as
  the question you would put to the team that submitted them, not as a topic heading.
- sources: the observation ids the answer rests on.
- confidence: High, Medium or Low — how completely the observations answer the question. Low is
  the honest answer where they barely touch it.

OUTPUT

Return ONLY a JSON object of the form:

{"answer": "...", "points": ["..."], "unknowns": ["..."], "sources": ["O-001"],
"confidence": "Medium"}

If there is nothing to work from, return an empty answer, empty points, and unknowns describing
what would need to be supplied. No markdown fences and no text outside the JSON. Keep each string
value on a single line — use ordinary sentences and separate paragraphs with "  " (two spaces)
rather than line breaks.
