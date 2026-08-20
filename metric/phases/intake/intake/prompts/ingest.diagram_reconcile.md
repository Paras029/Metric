WHAT THIS IS

Below are readings of {{document}} — one per image, each taken by looking at that image alone.
Node references are prefixed with the image they came from, so `i2.n4` is node `n4` of image 2.

**These images each show the same workflow.** They are not fragments of one picture that has been
cut up; they are separate accounts of the same flow — a detailed version and a summary of it, the
same flow drawn twice in different notations, an updated copy beside an older one, the same journey
drawn once per channel.

So your job is to **reconcile them into one graph, not to stitch them end to end.**

WHAT THAT MEANS IN PRACTICE

**The same step drawn in two images is one step, not two.** This is the whole difficulty. Two
readings will call the same box different things — "Auth check" and "Verify identity", "Locked
out" and "Account locked" — because the pictures label it differently or one abbreviates. Match
them on what the box *does* and where it sits in the flow, not on the words. Declare it once, under
the clearest of the labels used, and put the other wording in `unresolved` so a reader can see the
two were treated as one.

**Do not chain one image onto the end of another.** If image 1 ends at "Dispute filed" and image 2
begins at "Call opens", that is the same flow drawn twice — not a flow that runs from one into the
other. Welding them makes routes the agent does not have, and every one of those is issued to the
model owner as a test of behaviour nobody built.

**The most complete account wins, and the others fill it in.** Where one image shows a branch in
detail and another compresses it to a single box, take the detail. Where one shows a branch the
others do not, keep it — an image drawn later, or drawn for a specialist audience, routinely
carries a path the summary leaves out. Adding a branch one picture shows is right; dropping a
branch because another picture omits it is not.

**Where two images genuinely disagree, say so.** The same box with two different outcome sets, a
branch that leads somewhere different in each, a step one shows and another explicitly routes
around. Declare the fuller reading and put the disagreement in `unresolved`, naming both images.
That is a question for the model owner, and it is exactly the kind of thing an independent review
exists to surface — two versions of the documentation that do not agree is a finding, not noise to
be smoothed over.

THE READINGS

{{readings}}

{{vocabulary}}

Each image's own reading already classified every box's `kind`, and that classification
stands: a node read as `terminal` is terminal, a node read as `state` is not — even where
the arrow leaving it points at a box the readings did not fully resolve.

MAKE IT JOIN UP

Before you answer, check your own graph: every outcome you named should lead to a state, and every
state other than the start should be reached by an outcome you named. Where a reading leaves an
arrow's destination unclear and no other image settles it, still declare the outcome and put the
missing destination in `unresolved`.

**There should be one opening state**, since these are accounts of one flow. Two states reached via
`Start` means you have kept two versions of the same beginning apart; look again at whether they are
the same step under different labels. If they genuinely are two entry points that the flow has,
keep both and say so in `unresolved`.

Do not invent a step no reading described. And do not declare the same step twice under two
labels — that is the failure this pass exists to prevent, and it shows up downstream as two
identical scenarios with different ids.

ALSO RECORD WHAT THE DIAGRAMS ESTABLISH IN PROSE

Separately from the graph, note anything the images say that a reader of the documentation should
know, as observations under the key each most directly informs:

{{facets}}

OUTPUT

Return ONLY a JSON object of the form:

{"capabilities": [{"id": "CAP-01", "name": "...", "type": ""}],
 "decisions": [{"id": "DEC-01", "name": "...", "outcomes": ["Pass", "Fail"], "capability_id": "CAP-01",
                "inputs": "", "input_source": "User", "max_attempts": 1, "outcome_condition": ""}],
 "states": [{"id": "S-00", "reached_via": "Start", "description": "...", "next_decisions": ["DEC-01"],
             "is_terminal": false, "outcome_type": ""}],
 "observations": [{"facet": "...", "statement": "...", "quote": "...", "locator": "image 2, top left"}],
 "unresolved": ["..."]}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
