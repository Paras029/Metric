WHAT THIS IS

The image or images below were submitted as part of the documentation for an AI agent, and are
almost always a workflow or architecture diagram. An independent team is working out from this
documentation what the agent actually is, so that it can be tested.

A diagram is often the single most useful artefact in a submitted pack, because it states the
branching structure that the prose leaves implicit. Read it carefully.

IF SEVERAL IMAGES ARE ATTACHED

They are one flow, split up because it was too long to fit in a single picture. Read them as a
sequence and follow the connections between them. A branch that leaves the bottom of one image
and continues at the top of the next is one branch, not two.

WHAT TO BRING BACK

For every element you can read:

- Boxes and the labels on them: steps, states, decision points, systems.
- Arrows and their labels: what leads to what, and on what condition. The label on an arrow
  leaving a decision is usually the outcome that selects it, and it matters as much as the box.
- Anything that ends the flow: a terminal state, a handoff to a person, an abandoned path.
- Swimlanes, groupings, colour keys and legends, where they carry meaning.

Record each as its own observation, under the key it most directly informs:

{{facets}}

WHAT NOT TO DO

Do not infer a step that is not drawn. If an arrow leaves a box and its destination is cut off or
illegible, record what you can see and say the destination is unclear — a guessed branch becomes a
test of something that may not exist.

Do not tidy the vocabulary. If a box says "Auth check", record "Auth check" rather than "identity
verification": the words on the diagram are the words the rest of the documentation will use.

Where the image is too low-resolution, cropped, or otherwise unreadable, say so plainly in a
single observation under `use_case` rather than guessing at the content.

THE SOURCE

Image file: {{document}}

OUTPUT

Return ONLY a JSON object of the form:

{"observations": [{"facet": "...", "statement": "...", "quote": "...", "locator": "..."}]}

`quote` is the text as it appears on the diagram — a box label, an arrow label — and `locator`
says where in the image it sits, for example "top left", "third row", or the image's own number
where several are attached. Statements read off a diagram cannot be checked against a document,
so they are recorded for human confirmation; be conservative accordingly.

No markdown fences and no text outside the JSON. Keep each string value on a single line.
