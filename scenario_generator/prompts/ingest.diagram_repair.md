WHAT THIS IS

A workflow was read out of {{document}} and turned into the structure below. Checking that
structure against itself found the specific holes listed underneath it — places where it does not
join up, and therefore places where the reading is demonstrably incomplete.

The images are attached again. Look at them once more, with these particular holes in mind.

This is not a request to read everything again from scratch. The structure below is mostly right.
What is being asked is narrow: settle these named points, and return the corrected structure.

WHAT WAS READ

{{structure}}

WHAT DOES NOT JOIN UP

{{problems}}

HOW TO ANSWER THEM

Each of these is one of a small number of things, and it is worth knowing which before you look:

- **An outcome that leads nowhere.** An arrow was recorded leaving a decision and its destination
  was not. Find that arrow in the images and follow it. It ends somewhere: another box, a
  terminal, or off the edge of one image and into another.
- **A state nothing reaches.** A box was recorded with no route into it. Find what points at it.
- **A branch with fewer than two outcomes.** Either the other arrows leaving that box were missed,
  or it is not a branch point at all and is a step that happens to be drawn as a diamond.
- **A name that does not match.** Two entries refer to the same thing by different words. The
  diagram has one of them written on it; use that one.
- **A state that neither ends nor continues.** Look at whether anything leaves that box. If
  nothing does, it is terminal, and it needs the outcome type that says how the interaction ended.

Where the images genuinely do not settle a point — the arrow really does run off the page and no
other image picks it up, the label really is illegible — say so in `unresolved` and leave the
structure as it is on that point. **Do not invent a destination to close a hole.** A branch whose
end is honestly unknown is a gap a person can fill; a branch with a plausible invented end is a
test of something that may not exist, and nobody will ever notice.

WHAT TO RETURN

The **whole corrected structure**, in the same shape as below — not a list of changes. Carry over
everything that was already right; an entry you leave out is an entry you are saying should not
be there.

OUTPUT

Return ONLY a JSON object of the form:

{"capabilities": [{"id": "CAP-01", "name": "...", "type": ""}],
 "decisions": [{"id": "DEC-01", "name": "...", "outcomes": ["Pass", "Fail"], "capability_id": "CAP-01",
                "inputs": "", "input_source": "User", "max_attempts": 1, "outcome_condition": ""}],
 "states": [{"id": "S-00", "reached_via": "Start", "description": "...", "next_decisions": ["DEC-01"],
             "is_terminal": false, "outcome_type": ""}],
 "unresolved": ["..."]}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
