WHAT THIS IS

Below are independent readings of {{document}}, each written by looking at one image on its own
without seeing the others. Together they are one workflow that was split across several images
because it did not fit in a single picture — or, if only one reading follows, they are the whole
of it. An independent team is working out from this documentation what the agent actually is, so
it can be tested.

Your job is to put the readings together into that one workflow — follow the connections between
images; a branch that a reading said left the bottom of one image and another reading picks up at
the top of the next is one branch, not two — and then pull out of the assembled whole exactly what
the rest of this documentation needs to know.

THE READINGS

{{readings}}

WHAT TO BRING BACK

For every element the assembled workflow establishes, record one observation under the key it
most directly informs:

{{facets}}

Where two readings describe the same box or arrow from different images — an element repeated at
the join, for orientation — record it once. Where one reading said an arrow ran off the edge of
its image and another reading's content picks it up there, treat that as one continuous branch and
record where it actually leads. Where nothing picks it up, record that the destination is unclear
rather than guessing.

WHAT NOT TO DO

Do not infer a step that no reading described. Do not tidy the vocabulary — use the words the
readings recorded, which are the words on the diagrams themselves.

THE SOURCE

Image file(s): {{document}}

OUTPUT

Return ONLY a JSON object of the form:

{"observations": [{"facet": "...", "statement": "...", "quote": "...", "locator": "..."}]}

`quote` is the wording as recorded in the reading it came from — a box label, an arrow label — and
`locator` says where, for example "image 2, top left". Statements read off a diagram cannot be
checked against a document, so they are recorded for human confirmation; be conservative
accordingly.

No markdown fences and no text outside the JSON. Keep each string value on a single line.
