# Example intakes, and what to check with each

Five intake workbooks, built by `build_examples.py`. Each one is a valid upload: take it to the
intake stage, provide it as the intake workbook, run **Intake** and then **Workflow**, and compare
what comes out against the notes below.

Rebuild them at any time:

    python examples/intakes/build_examples.py

They exist because capability scoping changes what a scenario *is*, and that change reaches every
stage after the workflow. The things worth checking are not all in the scenario count.

---

## 1. `1_disputes_three_blocks.xlsx` — the canonical shape

Three capabilities in sequence. Identification hands on at two different positions (identified by
card details, identified by one-time code), so verification is walked from each.

**Expected:** 14 scenarios — 4 identification, 6 verification, 4 charge handling.

Check:

- Verification produces **two sets** of scenarios with different Starting Situations, one saying
  the cardmember was identified from card details and one saying by one-time code. If both sets
  carry the same text, entry-scoping is not working.
- Charge-handling scenarios are seeded at **"Verified cardmember on a clear account"**, not at
  "The chat opens". A block scenario that claims to start at the beginning is the main defect
  this design can have.
- In the issued pack (`data_template.xlsx`, Scenarios sheet), **Starting Situation** carries the
  full precondition sentence, not just a state name. A tester reading only a state name will open
  a fresh session and run the wrong conversation.
- Turn plans for verification scenarios should **not** contain turns that identify the cardmember
  again. Those turns test identification, and would leave verification untested. This is the one
  the model can still get wrong — worth reading three or four of them.
- Change one capability's span in the interface (Capabilities section, "Entered at" /
  "Hands on or ends at"), save, and confirm later stages go **out of date** and the count changes
  on the next Workflow run.

## 2. `2_travel_no_spans.xlsx` — the fallback, and the before picture

Same depth, three capabilities declared, **no spans drawn on any of them**.

**Expected:** 9 scenarios, all with an empty Capability column and no precondition.

Check:

- It walks the whole graph start to finish, exactly as the tool did before this change. An
  undivided intake is the ordinary state of a use case on its first run, and this must not
  produce an empty scenario space.
- The Capabilities section shows no "N of M capabilities have no span" warning, because *none*
  of them has one — the warning only fires when the spans are partly drawn, which is the
  genuinely confusing state.
- Starting Situation on the issued pack falls back to the state description.

## 3. `3_onboarding_deep_with_retries.xlsx` — retries and a route that goes backwards

Four capabilities. `DEC-03` allows three attempts, `DEC-04` two, and a document mismatch sends the
applicant **back** to document capture — a loop inside one block.

**Expected:** 16 scenarios across four blocks (3 / 5 / 4 / 4), against 19 walked whole.

Check:

- The loop stays **inside** the document-capture block. No scenario should walk from document
  capture back out into eligibility.
- Retry-bounded outcomes still appear. An outcome only reachable on a second or third attempt
  gets a focused route from inside its own block — check that every declared outcome of every
  decision appears somewhere in the space.
- No scenario exceeds `MAX_DEPTH` silently. If the run log warns about the depth cap, the span
  boundaries are drawn too wide.

## 4. `4_spans_drawn_wrongly.xlsx` — four ways to draw a span badly

Deliberately broken, four different ways. **Nothing here should fail silently.**

| Capability | What is wrong | Expected behaviour |
|---|---|---|
| CAP-01 | Exit names `S-99`, which does not exist | The bad id is dropped; the rest of the span is honoured |
| CAP-02 | Has an entry but **no exit** | Refused as a block, with a warning in the run log. Contributes **no scenarios** |
| CAP-03 | Entry `S-04` is not any other block's exit | Walks fine, but nothing hands on to it |
| CAP-04 | Overlaps CAP-03 — both claim `S-04` | Legal, and that region is walked **twice** |

**Expected:** 7 scenarios, from three spans. CAP-02 contributes nothing.

Check:

- The Capabilities section shows the warning: **1 of 4 capabilities have no span**. This is the
  most important check in the file — a block contributing zero scenarios is the quietest way for
  this feature to go wrong, and the count on screen is the only thing that surfaces it.
- The run log names CAP-02 specifically.
- Scoped count (7) is **higher** than the whole-graph count (5). Overlapping spans duplicate work,
  and a shallow graph does not benefit from being divided at all. Both are expected; see below.

## 5. `5_wide_chain_scale.xlsx` — the size where this actually matters

Four blocks, each resolving five ways: three that carry on and two that stop. This is what a real
identification → verification → decision agent looks like once every branch is declared.

**Expected: 200 whole-graph routes, 29 scoped scenarios.**

Rebuild it at other depths to see the trajectory (`wide_chain(directory, blocks=n)`):

| blocks | walked whole | walked per block |
|---:|---:|---:|
| 2 | 20 | 13 |
| 3 | 65 | 21 |
| 4 | 200 | 29 |
| 5 | 605 | 37 |
| 6 | 1003 † | 45 |

† At six blocks the whole-graph walk hits the `MAX_PATHS` cap of 1000 and **truncates**. Worth
knowing on its own: at real depth the old enumeration was not merely large, it was returning an
incomplete scenario space and saying so only in a log line.

---

## What does *not* improve, and why

On examples 1 and 3 the counts barely move (14 → 14, 19 → 16), and on example 4 the scoped count
is higher. That is correct and worth understanding before reading anything into a number:

**Only routes that hand on multiply.** A block whose outcomes mostly *end* the interaction
contributes additively to a whole-graph walk already, so there is nothing for scoping to collapse.
Meanwhile every entry into a block is walked from separately, which costs a little. A narrow agent
divided into capabilities produces slightly more scenarios, not fewer.

The gain is entirely in blocks with several continuing outcomes, and it compounds with depth —
which is example 5, and which is the real use case.

---

## Things to check that are not about counts

- **Scenario ordering.** The pack is ordered by capability first, so it reads in the order the
  agent works. A tester works through one block at a time.
- **Materiality peer grouping.** Redundancy is now measured within a block *and entry position*.
  Two verification scenarios entered different ways are not peers. Check the materiality
  rationale does not call a scenario redundant against one from a different block.
- **Coverage, if transcripts are submitted.** A conversation is credited to the capability it
  **ends in**, so earlier capabilities it passed through read as less exercised than they were.
  The coverage summary states this explicitly under "Counted per capability". This understates
  the model owner's coverage rather than overstating it, which is the safe direction — but it is
  a real limitation and the fix is to let one conversation match one scenario per capability.
- **Round trip.** Capability and Precondition are columns on the Scenario_Metadata sheet. Open a
  metadata workbook, confirm both are populated, and confirm a later stage re-run keeps them.
