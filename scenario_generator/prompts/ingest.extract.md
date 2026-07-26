WHAT THIS IS FOR

An independent team is about to build a test benchmark for an AI agent it did not build. The
benchmark can only test what is known about the agent, so what you find here decides what can be
tested. Anything real that you miss becomes a gap in the validation; anything you invent becomes
a test of something that does not exist.

Most of what you are reading will not be relevant. Documentation of this kind carries approval
histories, infrastructure detail, staffing and change logs. None of that describes how the agent
behaves in a conversation. Pass over it. Extracting nothing from a passage is a normal and
frequent outcome.

WHAT TO LOOK FOR

Return claims only under these facets, using the exact key given:

{{facets}}

THE SOURCE

Document: {{document}}
Location: {{locator}}

Each passage below is preceded by its own location in square brackets. Use the most specific
location that covers the words you quote.

---
{{chunk}}
---

WHAT TO RETURN FOR EACH CLAIM

- facet: one of the keys above, exactly as written.
- statement: one sentence, in your own words, saying what the document establishes. Self-
  contained -- it will be read on its own, away from this passage.
- quote: the words from the passage that support the statement, copied exactly. At least one
  full sentence. Copy it character for character, including its punctuation and capitalisation.
  Do not tidy it, join separated lines, correct an error in it, or shorten it with an ellipsis.
  This is checked against the document; an approximated quote fails the check and the claim is
  thrown away.
- locator: the bracketed location the quote came from.

RULES THAT DECIDE WHETHER A CLAIM SURVIVES

1. One claim, one quote, one place. A statement drawing on two separated passages belongs as two
   claims, not one.
2. If you cannot quote it, do not claim it. This applies to anything you worked out from the
   document rather than read in it, however reasonable the inference.
3. Prefer the document's own vocabulary. If it calls something a "case", do not translate that to
   "ticket" -- the testers will be writing conversations in this domain's language.
4. Do not restate the same fact under several facets to be safe. Choose the facet it most
   directly informs.
5. A passage with nothing relevant returns an empty list. This is expected, not a failure.

OUTPUT

Return ONLY a JSON object of the form:

{"claims": [{"facet": "...", "statement": "...", "quote": "...", "locator": "..."}]}

If the passage contains nothing relevant, return {"claims": []}. No markdown fences and no text
outside the JSON. Keep each string value on a single line.
