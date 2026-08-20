WHAT THIS IS FOR

A first reading of the submitted documentation left the questions below unanswered. Before they
are put to the model owner, they are put back to the documents once more.

Two things make this worth doing rather than skipping. A first reading answers each question from
what it was looking for at the time, and a question asked directly is answered by material the
first pass had no reason to connect. And every question that survives to the model owner is a delay of
days, so a question answerable from what they already sent should not be asked at all.

WHAT YOU ALREADY ESTABLISHED

The account built from the first reading. Use it: an outstanding question is often settled by
putting two of these together, or by noticing that one already implies the answer.

{{established}}

THE OUTSTANDING QUESTIONS

{{questions}}

THE DOCUMENTS

{{corpus}}

HOW TO ANSWER

Search the documents specifically for each question. Look in the places a first reading would
skim: tables, appendices, footnotes, configuration listings, glossaries, screenshots described in
captions, sections whose headings do not suggest they are relevant.

An answer may combine what several passages say, and may draw on what was already established
above. What it may not do is go past the evidence:

- `answered` — the documents settle it. Give the answer and quote what settles it.
- `partial` — the documents narrow it without settling it. Put what is now known in `answer` and
  what is still missing in `still_open`. This is a real result and both halves are used: what you
  establish is kept, and it is the `still_open` wording rather than the original question that is
  put to the model owner, so make it a question that can be answered on its own.
- `unanswered` — the documents do not address it. Say so plainly.

Do not resolve a question by inference from how such systems usually work. A question wrongly
marked answered is worse than one left open, because the open one gets asked and the wrong one
gets built on.

WHAT TO RETURN

For each question, in the order given:

- `id`: the tag the question was given above, in square brackets — `Q1`, `Q2`, and so on. This is
  what pairs your answer with its question, so it has to be right; the wording is a fallback.
- `question`: the question, copied as it appears above.
- `status`: one of the values listed above.
- `answer`: what the documents establish. Empty where they do not.
- `evidence`: list of `{"quote": "...", "document": "...", "locator": "..."}` supporting it.
  Empty where there is none.
- `still_open`: for `partial`, what remains to be asked. Empty otherwise.

OUTPUT

Return ONLY a JSON object of the form:

{"resolved": [{"id": "Q1", "question": "...", "status": "answered", "answer": "...",
"evidence": [{"quote": "...", "document": "...", "locator": "..."}], "still_open": ""}]}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
