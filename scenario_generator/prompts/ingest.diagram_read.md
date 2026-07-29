WHAT THIS IS

This image is one of {{position}} submitted as documentation for an AI agent, almost always part
of a workflow or architecture diagram. It may be the whole diagram, or one part of a larger flow
that continues in another image. An independent team is working out from this documentation what
the agent actually is, so it can be tested.

Read this image on its own and describe everything it shows. A second pass will be given every
image's description together and will do the work of putting them into one workflow — your job
here is only to say, fully and accurately, what this one image contains.

WHAT TO DESCRIBE

Everything the image shows, in plain prose:

- Every box and the label on it: steps, states, decision points, systems.
- Every arrow and its label: what leads to what, and on what condition. The label on an arrow
  leaving a decision is usually the outcome that selects it, and matters as much as the box.
- Anything that ends the flow here: a terminal state, a handoff to a person, an abandoned path.
- Swimlanes, groupings, colour keys and legends, where they carry meaning.
- Anywhere the flow appears to leave the edge of this image without ending — an arrow running off
  the top, bottom or a side with nowhere left to go. Say which edge, and what the arrow was
  labelled if anything; that is usually where the flow continues in another image, and the pass
  that reads your description alongside the others is what reconnects it.

Use the words on the diagram. If a box says "Auth check", write "Auth check" rather than
"identity verification" — the rest of the documentation uses the diagram's own words, and a
tidied vocabulary here would stop the two from matching up later.

Do not infer a step that is not drawn. If an arrow's destination is cut off or illegible, say so
rather than guessing what it might be.

Where the image is too low-resolution, cropped, or otherwise unreadable, say so plainly instead
of guessing at the content.

THE SOURCE

Image file: {{filename}} (image {{position}})

OUTPUT

Return ONLY a JSON object of the form:

{"description": "..."}

One string, as long as it needs to be, in plain prose rather than further nested structure. No
markdown fences and no text outside the JSON.
