# How it works

The companion to [README.md](README.md), which covers installation, launch and the shape of the
tool. This page is what each stage actually does, why it does it that way, and every setting you
can turn.

---

## Contents

- [The idea](#the-idea)
- [The stages in detail](#the-stages-in-detail)
  - [1. Intake drafting](#1-intake-drafting)
  - [2. Workflow](#2-workflow)
  - [3–6. Scenario space, variations, materiality, review](#36-scenario-space-variations-materiality-review)
  - [7. Coverage](#7-coverage)
  - [8. Summary](#8-summary)
- [The intake workbook](#the-intake-workbook)
- [Tuning](#tuning)
  - [What a model call is actually sent](#what-a-model-call-is-actually-sent)

---

## The idea

A validation team is asked whether an agentic AI system is fit for the business purpose it was
built for. It did not build the agent, and it does not take the model owner's testing at face
value: it constructs its own scenario space, issues it, and judges what comes back.

Two properties make that scenario space worth anything.

**It is exhaustive over what was declared.** The agent is described as a decision graph, and the
graph is walked. Every distinct route through it becomes one scenario, so coverage of the declared
design is provable rather than asserted. Where the walk misses a declared outcome, that outcome
gets its own focused scenario.

**The answer key never leaves the building.** The data template carries what to test and how to
test it, and nothing about what a correct response looks like. Expected outcomes, decision paths
and categories stay in the scenario space metadata, which is yours.

Everything else in the tool is in service of one of those two.

---

## The stages in detail

### 1. Intake drafting

Reading the documentation and drafting the declaration are one stage, because they are one
job: the reading exists in order to be drafted from and nothing between them is yours to decide.
Skip the reading entirely by uploading a completed intake workbook, which is then read and never
written to.

The documents are read **whole**. Every submitted file is turned into text with its locators
intact, joined into one corpus, and put to the model in a handful of calls that each answer a
group of related questions across all of it at once. That is what makes the reading good: a
threshold in an appendix and the process it governs in section three are in front of the model
together rather than in two calls that never meet.

Formats: `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.xlsm`, `.csv`, `.md` and `.txt`, plus `.png`, `.jpg`
and `.jpeg` diagrams described with a vision-capable model. Spreadsheets are read sheet by sheet
with the header repeated on each passage, because a rules table is where the thresholds usually
live. A scanned PDF has no text layer and is reported as unreadable rather than read as empty —
ask for a text-based copy.

Every submitted document is named in the reading prompt, and the result says which ones each
answer actually rested on. A file that informed nothing is reported: it is either irrelevant or it
was passed over, and those need different responses.

**Grounding.** Every answer cites verbatim quotes, and each quote is checked against the corpus by
a deterministic matcher. A quote that cannot be located is dropped, and an answer that loses all of
its evidence is marked for confirmation rather than trusted. The match is tolerant of how text
arrives — PDF extraction breaks words across lines, turns quotation marks into typographic
variants and scatters whitespace — but not of what it says: a reworded quote does not survive. The
match must also be *local*, scored inside a window around the quote's best anchor, so a sentence
that appears nowhere cannot be assembled from fragments scattered across a large pack.

**The resolution sweep.** Questions the first reading left open are put back to the documents,
directly, more than once. A question asked directly is often answered by material a general
reading had no reason to connect, and every question that survives to the model owner costs days.
`resolve_passes` controls how hard it tries.

**Workflow diagrams are read as graphs, not as prose.** A diagram *is* the intake's decision and
state sheets — a box with branching arrows is a decision, an arrow's label is an outcome, the box
it lands in is a state — so the reading keeps that structure the whole way rather than flattening
it to sentences and asking a later pass to rebuild a graph from the sentences. Three passes:

1. **Each image on its own**, into an explicit list of boxes and arrows. Enumerated rather than
   described, because a box missed in a paragraph vanishes silently, where a box missed in a node
   list shows up as an arrow pointing at nothing. Arrows that run off the edge of the image are
   recorded as exactly that — they are where one image joins another.
2. **All the readings together**, put into one graph and written in the intake's own vocabulary:
   decisions with named outcomes, states with `reached_via`, terminal flags and outcome types.
3. **Whatever that graph cannot account for, back to the images.** The graph is checked against
   properties it must have to be walkable at all — every outcome leads somewhere, every state is
   reached by an outcome that exists, a branch has more than one branch — and the specific
   failures are named back to the model with the images still attached: *"DEC-03 outcome Timeout
   leads nowhere; follow that arrow"*. Once, not until clean.

The resulting graph is carried through to the intake drafter **as structure**, alongside the prose
context, so drafting becomes confirming and completing a graph rather than rebuilding one from
sentences about it. It also appears in the context document, so you can read what was extracted.

**One image skips the middle pass.** There is nothing to join it to, so it is read straight into
the intake's vocabulary in a single call — one call rather than two, and without a prompt whose
opening paragraphs are about arrows running off the page into other pictures, which for a single
submitted diagram describes a situation that does not exist. The audit and the repair pass still
run: a lone diagram read with a hole in it is exactly as unwalkable as several.

**Several images are put together in one of two ways, and you choose which.** A control under the
workflow-diagrams card, shown once there is more than one image:

- *One workflow, split across these images* — the default. Pieces of one picture: an arrow leaving
  the edge of one image is followed into whatever picks it up in another.
- *Each image shows the same workflow* — separate accounts of one flow, not fragments. A detailed
  version and a summary, the same flow in two notations, an updated copy beside an older one. The
  same step drawn twice is declared once, the fullest account wins and the others fill it in, and
  where two images genuinely disagree the disagreement is recorded rather than smoothed over —
  two versions of the documentation that do not match is a finding.

Applying either reading to the other's input goes wrong quietly. Stitching two drawings of one
flow welds the end of the first onto the start of the second and enumerates routes the agent does
not have. Reconciling genuine pieces folds the end of one picture into the start of the next as
"the same step under a different label". Neither is visible in the result without reading the whole
graph back against the pictures, which is why it is asked rather than guessed at. Changing it makes
a completed reading out of date, the same as adding a file.

**The workflow-diagrams card takes images and nothing else.** A PDF or a spreadsheet dropped there
is refused, naming what the heading takes — it would otherwise be filed under "Workflow diagrams",
read as the ordinary document it is, and leave nobody able to work out why the diagram they
submitted is not in the graph. Documents describing the flow in prose belong under model
documentation or supporting material, where they are read as text.

An image that fails on its own is dropped rather than losing the rest; the diagrams are only
reported unreadable if every one of them was, or if nothing could be made of them together. A
repair pass that returns nothing usable leaves the first reading exactly as it was — a second look
can improve a reading, never damage it.

**Redaction.** With `PII_REDACTION=on`, every passage a document is read into is redacted before it
is joined into a corpus and before any of it reaches a model call — names, account numbers and
other business-sensitive material never leave your machine unredacted. This is not a best-effort
pass: if the redaction engine cannot be reached, ingestion stops rather than sending the document
through unredacted.

A **Redact** checkbox next to each uploaded model-documentation or supporting file marks that one
file to be redacted the next time Documents runs, whether or not `PII_REDACTION` is on for
everything else — one particularly sensitive upload does not require switching redaction on for
the whole pack. It has no effect the other way round: there is no per-file opt-out once
`PII_REDACTION` is on. Diagrams have no text to redact, and the model owner's conversations
never reach this reading at all, so neither shows the checkbox.

#### The declaration itself

The intake is the authoritative description of the agent, and the boundary of what can be tested.
Anything absent from it is absent from the scenario space. Draft it from the documents, upload one you
already have, or start from a blank template — see [The intake workbook](#the-intake-workbook).

**The draft is checked and put back to the model once.** A single call over a long context
routinely leaves the small structural things out: a branch with only one named outcome, an outcome
leading to a state nobody declared, a state nothing reaches. None of those are matters of opinion —
the graph cannot be walked without them, so whatever they concern is silently never tested — and
the answer is usually a paragraph away in the documentation the first pass had already read. So
the draft is read back off disk, audited deterministically by the same check that produces the
questions below, and whatever it failed is named back to the model with the documents still in
hand. Once, not until clean, and never destructively: a repair that does not come back, or comes
back empty, leaves the first draft exactly where it was.

**Running the stage again revises rather than redrafts.** This is the difference between a second
run that helps and one that undoes the first. Everything that has happened since — answers to the
questions below, notes typed anywhere, a document read again — is new information about a
declaration that *already exists*, and a fresh draft has no way to tell a correction somebody made
by hand from something it should re-derive from nothing. So the current declaration goes to the
model as what to revise, with instructions to carry forward everything the new information does not
touch, and the structural check runs again afterwards.

Two guards on that. A revision that comes back with less than 60% of the decisions or states it
was given is discarded and the current declaration kept, because a reply that collapsed the graph
did not do what it was asked. And a workbook *you* uploaded is never written to at all: a re-run
reads it and reports on it, and "Revise the intake with these answers" writes the revision to the
tool's own file rather than over your upload, which stays downloadable.

**Gaps in the declaration are questions addressed to the row that needs them, not to the
documents.** Once a workbook exists, drafted or uploaded, it is read for where it is structurally
thin — a decision with fewer than two outcomes, a capability with no type, a terminal state with
no outcome type, a state nothing reaches, a persona with no stated objective — and each gap
becomes a question addressed to that specific row (see `core/gaps.py`). A question about the
*evidence* — "what are the known risk areas?" — says which section of the documents was thin but
never which decision, state, capability, tool or persona actually needs filling in, and it has no
single right length of answer. "What should DEC-05's second outcome be called, and what decides
between the two?" can be answered in a sentence, because the question already says exactly what
row of the intake it fills in.

What the drafter itself flagged as inferred rather than read is folded in beside those, read back
from the workbook's own **Review This** sheet. Anything genuinely cross-cutting — no state marked
as the start, or a question the documents never addressed at all — is kept, at the bottom, once
the row-scoped questions have had first claim on your attention.

The triage judging what "blocks the intake" means is deliberately conservative: a scenario needs to
know that a branch exists and where it leads, not the exact value, threshold or wording that
selects it. An unstated dollar figure or timeout is not put to you as long as the branch it governs
is already named. Statements read off a diagram — which can run to dozens for one workflow image —
are grouped into one confirmation per part of the intake rather than one per statement.

**Where your answers, notes and late documents go.** Every note — typed anywhere, at any stage,
including your answers to the questions above — is handed to every model call from that point on:
the intake drafter and reviser, the scenario text, materiality, the review, coverage mapping. Each
carries the stage it was added at and the question it answers, so a later pass reads it as an
answer rather than as a loose remark, and they accumulate rather than replace. A late document goes
into the supporting material and is read the next time the Documents stage runs. Nothing re-runs by
itself, so anything added lands the next time you run a stage.

Notes are also the one part of the context that is never dropped to fit a budget — see
[What a model call is actually sent](#what-a-model-call-is-actually-sent).

Answering a gap question records a note, and nothing re-runs automatically. **Revise the intake
with these answers**, a separate action on the same page, is what folds them in: it hands the model
the *current* declaration — drafted, hand-edited, or both — alongside every note and answer, old and
new, with instructions to change only what the new information actually requires and carry
everything else forward untouched. This is deliberately not the same call as the first draft, and
deliberately not automatic: a plain redraft has no way to tell your hand correction from something
it should re-derive from scratch, and would silently discard it. Revise as many times as you like;
each pass sees everything answered so far.

**The graph picture.** **Square boxes are decisions**, **arrows are the states between them**
labelled with the outcome that took them there, and **stadium-shaped boxes are the ways an
interaction can end**. The shape carries that distinction rather than the colour, so it survives
being printed.

**Hover anything to follow one thread.** A declared graph of any size is a picture in which every
line crosses every other, and the question a reader actually has is never "what is the whole
shape" — it is "where does *this* come from and where does it go". Hovering a box holds it, every
arrow touching it, and the box at the far end of each of those at full strength, and drops the
rest of the graph back to a faint outline until the pointer leaves. Hovering an arrow does the
same for that arrow and the two boxes it joins. One hop, not the whole downstream tree —
highlighting everything reachable from a box near the start lights the entire graph and answers
nothing. With scripting off the picture renders exactly as it always did and every box and arrow
still carries its own tooltip.

**The same ending is drawn once.** A drafted intake usually writes one terminal state per decision
outcome, so "escalated to a case handler" arrives as four states with four ids — which drawn
literally is four endings, and is what fills the right-hand columns with boxes that are all the
same box. States whose description *and* outcome type match exactly are drawn as one box with
every route arriving at it, the ids it stands for are named in its hover, and the duplication is
reported above the picture as something to tidy in the intake. Endings that differ by a word are
left alone: two endings that differ may well be two endings, and merging them would hide a route.

**The picture and the walk read the declaration the same way.** They used not to: the walk parses
a `Reached Via` cell with a regex finding every `DEC-nn=Outcome` pair in it, while the picture
compared the whole cell for exact equality. So a state two outcomes converge on
(`DEC-01=Fail, DEC-02=Unclear`) and an outcome carrying its retry bound (`DEC-01=Fail
(attempt<3)`) were both enumerated, written up and issued — and drawn nowhere. A route that is
invisible on the one screen anybody checks the declaration on is a route nobody questions, so
there is now one authority on what leads where and the picture asks it.

Below the picture, what the declaration leaves out — a branch with one outcome, an outcome leading
nowhere, a state nothing reaches — each said as something you can go and fix.

A retry — a decision whose failed outcome leads back to itself or an earlier point — is drawn as a
dashed loop through a lane on the right rather than a straight arrow back up through the rows in
between. That is most of what makes a branchy graph look tangled: an ordinary top-to-bottom flow
with every retry routed the same way it happens (Max Attempts almost always means a loop) rather
than crossing back over everything drawn since.

You can also try a change before committing it. Add a decision or a state and it appears in the
graph immediately, dashed; anything not yet connected sits in a row underneath until you give it
somewhere to go. Press *Write these into the intake* and the rows are appended to the workbook —
everything already in it, including your own edits, is left alone. For changes to what is already
declared, edit the workbook and upload it again.

**Tidying the graph** is an optional action once a workbook is in place. **Look for tidying
opportunities** sends the whole declared graph to a model in one call and asks for two kinds of
proposal, shown on the page rather than written anywhere until you act on one.

*Reconnections* — a decision or state that does not connect to the rest of the graph, with a
specific fix: which state a stray decision should be reached from, or which decision outcome a
stray state should be reached via.

*Consolidations* — decisions that look like alternate routes to the same fact rather than genuinely
different branches, the way a caller might be identified by the last four digits of an SSN, the
full SSN, or a card number, with nothing afterwards depending on which one was used. Every one of
those multiplies the scenario space by every combination without testing anything additional past the
point where they converge; merged into one decision, the same downstream behaviour is tested at a
fraction of the cost. The pass is told which decisions are *structural* candidates — every outcome
of each lands on the same downstream point as every outcome of the others — as a computed hint,
not a verdict; whether merging is actually a good idea is still its judgement to make, weighing
what the capabilities involved are for and how central the decision is to the use case.

Applying either kind writes straight to the workbook and invalidates everything after the intake
stage, the same way a corrected upload would. A consolidation's merge keeps every capability the
merged decisions used as a note against the new one, since the workbook's Triggering Capability
column holds only one id; the rest survive as text a person can see rather than as a structural
link. Dismissing a proposal discards it without touching anything.

### 2. Workflow

Deterministic, and no model is called. The graph is walked exhaustively: every distinct route
becomes one scenario, with its route recorded as the expected outcome. Routes are deduplicated by
their sequence of (decision, outcome) pairs, and any declared outcome the walk missed gets its own
focused scenario, carried on to an ending like any other.

**A route runs from a start state to a state the intake marks as ending the interaction, and
nothing shorter.** That is what makes a scenario issuable: it is a conversation handed to the model
owner with an expected outcome behind it, and a route the declaration stops short of has no
expected outcome to have. There are three ways a walk stops short, and all three are the
declaration running out rather than the agent finishing:

- the outcome taken names a destination no state declares itself reached by;
- the state reached leads nowhere and is not marked as ending the interaction;
- every decision that state offers has used up its `Max Attempts`, or is out of scope.

None of these produce a scenario. The scenario space metadata holds routes that end at a declared
ending and nothing else, so a partial route cannot reach the data template, the coverage mapping or
any later judgement by way of it.

The first two are declaration gaps the intake stage already asks about, against the row that owns
them: an outcome with no destination, and a state that leads nowhere without being marked as an
ending. Answer those and the routes through them come back as ordinary scenarios.

The retry case is the one worth being concrete about. If identity verification allows two attempts
and the second failure lands nowhere declared, that route is reported, not tested. Declare a second
state also reached by `DEC-01=Fail` — "locked out after the second attempt" — and the same route
becomes an ordinary scenario ending there. The rule is not that retry exhaustion is untestable; it
is that it is testable exactly when somebody has written down what it does.

A library of adversarial and non-functional probes is applied separately. Probes test properties of
the agent rather than routes through it — whether its instructions can be extracted, whether it
invents detail under pressure, how it behaves under provocation. The library is use-case-agnostic;
which probes apply is determined mechanically from what the intake declares, chiefly from
`L2 Capabilities.Type`.

The strength of that approach is that it is exhaustive over what was declared. Its weakness is that
it is bounded by what was declared, which is what the final review exists to push against.

### 3–6. Scenario space, variations, materiality, review

**Scenario space** writes each scenario up: a short name, a business description, and the tester
script. The writer is never shown the scenario's terminal state, so the expected outcome cannot
reach the pack through the text it writes.

The **name** is a handle rather than a summary — six words or fewer, about the situation and never
about the expected behaviour. It exists because a scenario space is three hundred rows in a spreadsheet
and three hundred rows on a page, and until it existed the only handle on any of them was the
first sentence of a description; those sentences all open the same way, because they describe the
same agent. One exists before any model runs, built from the route itself.

**What comes back is audited, and only the failures go back.** A name, a description that is one,
a turn plan with lines in it — and whether the text carries a distinctive phrase out of that
scenario's *own* ending. The writer is never told where a route finishes, but it is told the
outcome of every step on the way, so a route ending in a lockout can be written up as "and the
account is locked" with nothing having told it so. The match is on three consecutive content words
rather than on word overlap: a description of a dispute and an ending about a dispute share the
word "dispute" honestly. Failures go back once in a single batched call with the fault named; a
rewrite that is not strictly cleaner than what it replaces is discarded, and anything still
leaking is logged by id.

**Variation space** is not built yet. It reads the scenario space, writes it back unchanged, takes its
snapshot like every other scenario stage, and says so on its own result card — so the pipeline
already has the shape it will keep and nothing downstream changes when it is filled in.

**Materiality** assigns Low / Medium / High / Critical per scenario, which drives how many variations
the data template requests. It judges the set rather than each scenario in isolation: whether a
scenario is the worst thing here depends partly on what else is in the scenario space, and redundancy
is a real discount.

**Final review** is the last pass before the pack is issued. It settles materiality, checks each
scenario's declared category against what the route actually does, flags scenarios that are
redundant, under-specified or mis-scoped, and proposes additions for what enumeration could not
reach — everything the intake's author did not think to declare, and everything about this specific
business a generic probe library could not know.

It is three calls, not one. Materiality and flagging are one sweep over the scenario space; the
declared-ending check is a second, coarser one; proposing additions is a third. The two sweeps are
different readings — one asks what failure would cost, the other how the interaction ends — and a
single prompt carrying both tends to answer the first well and the second as an afterthought.

Every verdict is written to its own column beside the value it disagrees with, never over it, so
both readings stay visible and a person rules. The review cannot remove anything: flagging a
scenario as redundant is a recommendation.

From stage 5 onward the page shows the scenario space itself: what each scenario asks the agent to
do, what it is judged to be worth and why, and anything the review flagged. Materiality can be
overridden and a flag dismissed from there, straight into the scenario space metadata.

Above the list is the same space as a grid — category down, materiality across, the count in each
cell. It exists for the one question a ranked list cannot answer: which combinations have nothing
in them. A hole has no row, so no amount of sorting will surface it, and a space with no critical
termination scenario looks complete right up until somebody asks. Every cell is a link that
narrows the list to it, so the grid is a control as well as a picture. The side panel carries the
shape of the whole pack alongside it — how many scenarios sit at each tier, how many variations that
adds up to, and how much of it the model owner's testing has already exercised — because the
list in the middle is always a view of part of the scenario space and the panel answers what is in all
of it. Routes, expected outcomes and per-turn detail stay in the workbook — the screen carries the
reading, the workbook carries the record.

### 7. Coverage

Optional. It answers one question: what does the model owner's existing testing already cover, and
what does it leave untouched?

**What the stage reads is transcripts, not a list of scenario titles.** A list of titles is a claim
about the testing; the transcripts *are* the testing. Model owners who have grouped their
conversations under labels of their own are the exception rather than the rule, and requiring that
grouping would put the measurement out of reach of exactly the submissions that need it. Any
grouping that is supplied is carried through and assessed against where the conversations actually
landed — a group that splits across several scenarios is a disagreement worth someone's attention.

**The submission this stage asks for is `data_template.xlsx`, returned filled in.** That is the
same workbook stage 8 issues: the team completes the `Variation_Log` sheet, one row per scenario,
variation and turn, and sends it back. It is recognised outright, and because every row already
names the scenario it was run against, a submission returned whole is mapped with **no model calls
at all** — the id is the answer to the question the mapping pass exists to ask, so asking again
would be paying to rediscover something already written down. An id the current space does not
have still goes to the call: a template issued off an older space is a finding rather than
something to trust silently.

**Anything else gets one call to work out its shape.** Real submissions come out of a logging
system in whatever columns that system produces, and matching column headings against a word list
fails in the way that hurts most — silently. A column headed `Utterance` is found; one headed
`cust_msg_body_txt` is not, and the fallback rule ("the widest column carries the words") happily
picks the agent's reasoning trace and files it under the user. Nothing about that result looks
wrong; the coverage figure is simply built on the wrong column.

So the layout is asked about instead: one call, shown the header and a handful of rows, naming
which column holds the conversation id, which holds the words, which says who was speaking, and
how that column spells each side — `C` and `A` are as common as `User` and `Agent`. Every row is
then read in code from that answer, so it is one call for a file of any size. If the call cannot
be made, or names a column the file does not have, the heading-matched reader takes over and says
so. Which reading was used is always reported back to you, because a misread column halves a
coverage figure quietly.

Three layouts are read either way: one row per turn with a conversation id repeated down the
sheet; one row per conversation with the whole transcript in a cell; or a document with no table
at all, conversations separated by headings with `User:` / `Agent:` prefixes on each line.
Spreadsheets, CSV, Word, PDF and plain text all work.

**Probes count as evidence when the team ran them.** They go out in the same template as the
routes, so a team can run a probe and report it. That is no longer filed as "matched no scenario"
— which described a hole in their testing where the hole was in the reader. What coverage
*measures* is still the functional space only: a probe is a property of the agent rather than a
route through it, and putting probes in the denominator would move the figure without changing
the evidence.

**Transcripts can be redacted before anything reads them.** Tick *Redact* against the file on the
upload panel. This matters more here than anywhere else in the tool: the documentation describes
an agent, where these are real conversations with real customers in them. Redaction runs over the
whole submission in one pass, so one customer keeps one placeholder across every exchange they had.

**One conversation maps to exactly one scenario, and the match is decided by where the conversation
ends.** A scenario space scenario is a complete route to a specific ending, and some routes are prefixes
of others: a conversation that authenticates and stops is not the same test as one that
authenticates and then goes on to verify a charge, even though the second contains the first.
Filing the short one under the long one would report coverage that does not exist, so the matcher
is told explicitly not to. "No scenario fits" is a first-class answer — it means either the model
owner is testing something the scenario space never enumerated, which is worth knowing, or that
conversation is not a test of this agent.

**Coverage is a count, not a flag.** One conversation against a scenario and forty against it are
not the same evidence. *Represented* is a line drawn through the counts at a threshold you set on
the stage page; changing it recounts what is already mapped and rewrites the workbook, without
another pass over the transcripts. How much evidence is enough depends on how far the model owner's
testing is trusted, which is a judgement the tool has no basis for making.

The matcher's confidence in each mapping is shown beside the count, never folded into it. A
scenario with five low-confidence mappings and one with five high-confidence mappings both have
five, and which of those is convincing is exactly the sort of thing worth putting in front of a
person.

Each scenario is also annotated in the scenario space metadata, under `Owner Coverage`, with the count and the
conversation ids behind it. By default that is an annotation and nothing more — no scenario is
dropped, because whether running something already tested is duplicated effort or independent
confirmation is your call rather than the tool's.

### 8. Summary

Writes the data template to send and the scenario space metadata to keep.

The Issue stage offers a setting that narrows the pack to what the model owner under-covers; it is
off unless you turn it on, since leaving a scenario out says their evidence for it is accepted.
The scenario space metadata keeps every scenario regardless — narrowing what is *issued* must not narrow what is
on record.

The coverage annotation never appears in the data template either way. Telling the model owner
which scenarios you already consider answered would tell them exactly which ones to concentrate on.

---

## The intake workbook

| Sheet | Holds |
|---|---|
| `Guide` | Field-by-field instructions with examples |
| `L1 Use Case` | Name, objective, agent type, channel, handoff triggers, safety requirements, success criteria |
| `Personas` | The kinds of user the agent serves |
| `L2 Capabilities` | Each distinct thing the agent can do, typed |
| `L3 Decisions` | Branch points and their named outcomes |
| `L4 States` | Positions the interaction can occupy |
| `Tools` | Systems the agent calls |

Five fields carry more weight than their size suggests.

**`L2 Capabilities.Type`** — one of `Lookup`, `Transactional`, `Gating`, `Advisory`,
`PII-handling`. Decides which probes apply. A blank type silently drops the probes that would
have tested that capability.

**`L3 Decisions.Input Source`** — `User`, `Tool`, `Memory-Session`, `Memory-CrossSession`,
`System-Context` or `Document`. Only `User` steps become conversational turns, which is what
allows agents that plan internally, or run without a conversation, to be described.

**`L3 Decisions.Max Attempts`** — the retry bound for that decision. There is no global limit, so
a decision allowing three attempts produces three-attempt routes.

**`L4 States.Outcome Type`** — on a terminal state: `Happy path`, `Retry`, `Fallback`,
`Escalation` or `Termination`. Sets the category of every scenario ending there.

**`L3 Decisions.Out of Scope?`** — `Y` for a decision that has already been reviewed elsewhere and
is being reused as-is, the usual case being a plug-and-play sub-system covered by a separate
engagement. The decision stays in the sheet and stays in the graph picture, drawn muted, because
the graph is not honest without it — but no scenario is generated through it, and it does not
count against the completeness checks the intake stage reports. Flip it from the intake stage
itself (a checkbox beside each declared decision, in effect immediately) or in the workbook
directly; either way it takes hold the next time the scenario space is built.

A drafted intake includes a **Review This** sheet giving a confidence per section and the specific
points the draft could not settle. Read it before relying on the draft.

---

## Tuning

Everything below lives in `tuning.yml`, and every key also has an environment variable name that
wins over the file. See [README.md](README.md#configuration) for the two-file split and the tier
table.

**Edits take effect without a restart.** The file is re-read whenever it changes on disk, so a
value changed while the interface is running applies to the next call — no restart, no button. The
run states which file it is reading when it starts; if that line names a file you did not expect,
or says none was found, that is why a setting appears to do nothing. The file is looked for in the
working directory, then upwards from it, then beside the installed package, and `TUNING_PATH`
names it outright.

**A file that will not parse stops the run rather than being ignored.** Falling back to the
built-in defaults is the worse failure: every value the file was setting reverts at once — which
model each stage calls, the batch sizes, the budgets — and the run proceeds and produces plausible
output, so the mistake surfaces later as a scenario space that is subtly not the one you asked for. On
startup a broken file is a refusal to start, naming the line. If a file that was working is broken
by an edit *while a run is under way*, the settings from before the edit stay in force and the
problem is logged as an error; nothing reverts to a default mid-run.

### Per-stage overrides

A tier is shared by every call doing the same *kind* of work, which is coarser than every call
*site*: weighing a scenario's materiality and checking the review's declared category are both
Materiality-tier work, but they are two different calls in two different passes, and a scenario space
can want them tuned differently — a smaller model for the mechanical category check, the full one
for materiality itself.

**Every model call the tool makes is on this list, and every one of them takes the same
settings** — including its own model. `tuning.yml` carries an entry per call site with the model
line commented out and ready to fill in; the same thing is settable from the environment as
`LLM_STAGE_<KEY>_MODEL_ID`, and likewise `_MAX_TOKENS`, `_TEMPERATURE`, `_REASONING_EFFORT`,
`_MAX_ATTEMPTS`, `_BATCH_SIZE` and `_CONCURRENCY` (the last two only where the call batches).

Every field falls back to its tier's own setting where the stage does not override it, which itself
falls back to `LLM_MODEL_ID` — three levels, stage then tier then master, and setting nothing at
this level changes nothing.

| Stage key | Call site | Tier | Default batch |
|---|---|---|---|
| `INGEST_READ` | Reading each facet group from the whole corpus | Judgement | — |
| `INGEST_RESOLVE` | The resolution sweep | Judgement | — |
| `INGEST_DIAGRAM_READ` | Reading one diagram image on its own | Judgement | — |
| `INGEST_DIAGRAM_SYNTHESIZE` | Joining every image's reading into one graph | Judgement | — |
| `INGEST_DIAGRAM_REPAIR` | Putting unresolved points back to the images | Judgement | — |
| `INTAKE_DRAFT` | Drafting the intake from the evidence | Judgement | — |
| `INTAKE_REPAIR` | Filling in what the draft left structurally incomplete | Judgement | — |
| `STRUCTURE_REVIEW` | Proposing reconnections/consolidations on the intake | Judgement | — |
| `WRITER` | Writing scenario text | Standard | 8 |
| `MATERIALITY_ASSESS` | Weighing each scenario's materiality | Materiality | 10 |
| `WRITER` (repair) | Rewriting only the scenarios that failed the audit | Standard | one call |
| `REVIEWER_ASSESS` | Review's materiality + flagging sweep, and the adjudication | Judgement | 6 |
| `REVIEWER_CATEGORY` | Review's declared-category check | Materiality | 20 |
| `REVIEWER_PROPOSE` | Review's addition proposals | Judgement | — |
| `COVERAGE_MAP` | Mapping submitted conversations onto the scenario space | Judgement | 5 |

For example, `LLM_STAGE_REVIEWER_CATEGORY_MODEL_ID=some-cheap-model` moves only the category
check onto a smaller model, leaving materiality assessment on whatever `LLM_MATERIALITY_MODEL_ID`
(or `LLM_MODEL_ID`) says, even though both share the Materiality tier by default.

Materiality is split out from judgement rather than sharing it, because it is also the tier under
the most concurrent load: every chunk of the scenario space is sent at once, so a gateway hiccup there
means several simultaneous retries rather than one. It defaults to a shorter retry ladder for that
reason, and can be pointed at a smaller model independently of judgement if you are seeing
connection errors under load.

### Batching

"Batching" is two separate numbers, and they answer two different questions.

**Batch size** — how many rows go into the payload of *one* call. This decides how many calls a
fixed amount of work turns into: a space of 100 scenarios at a batch size of 10 is 10 calls; at
20, it is 5 larger calls. It does not change how much is sent in total, only how it is divided up,
and dividing it up too coarsely is what lets a single scenario's judgement get lost inside a call
that is weighing twenty others at the same time.

**Concurrency** — how many of those calls are ever in flight to the gateway *at once*.
`LLM_MAX_CONCURRENCY` caps this globally; it does not change call count or what any single call is
asked to judge, only how much they overlap. Turning it up does not make one call faster, but it can
make a whole stage finish faster by not waiting for calls to return one at a time.

The two compose: a smaller batch size with higher concurrency sends more calls, more of them at
once — usually the faster and cheaper-to-retry combination, since a dropped call repeats less
work. A larger batch size sends fewer, heavier calls, each risking more if one of them fails.

The default batch size differs by stage, and each was picked for what that particular call is
actually weighing, not from one shared number:

- **Scenario text** (8 scenarios a call) writes each one mostly independently — the only shared
  context is the use case and house style — so the chunk exists purely to amortise that shared
  preamble across several scenarios rather than resending it once per scenario.
- **Materiality** (10 a call) has to see enough of the scenario space at once to judge relative
  consequence — whether a scenario is "the worst thing here" depends partly on what else is in the
  same call — without the call growing so large that a single scenario's tier gets lost in it.
- **Review's assessment sweep** (6 a call) is the most demanding read per scenario: it is settling
  materiality, flagging redundant or under-specified scenarios, and doing it with the full
  reviewer field guide in view, so the chunk is kept small enough that each scenario still gets a
  considered look rather than a skim.
- **Review's category sweep** (20 a call) is a narrower, more mechanical judgement — does the
  declared outcome type actually match what the scenario does — so it tolerates a much larger
  chunk without the same loss of attention per item.
- **The adjudication** reuses the review's own batch size, and fires only on scenarios the two
  materiality readings put two or more tiers apart. A scenario space whose two readings agree pays
  nothing for it; one with a handful of conflicts pays one call.
- **Coverage mapping** (5 a call) is the smallest, because a transcript is many times the size of
  a scenario description and every call has to carry the whole scenario space alongside them for the
  match to be possible at all.

None of this is tuned to a model's context window; every call here is far short of it. It is tuned
to how much one call can weigh carefully at once, which is a much smaller number.

`LLM_STAGE_REVIEWER_ASSESS_BATCH_SIZE=4` and `LLM_STAGE_REVIEWER_ASSESS_CONCURRENCY=8`, say, sends
smaller, more careful review calls while keeping more of them in flight at once.

### What the progress bar counts

A running stage reports two things, because they answer different questions. The line says what is
being waited on right now; the bar says how much of the run is behind you.

The bar counts **finished** units, never started ones. That distinction is what keeps it honest:
the three document-reading calls go out together, so a bar that counted them as they were sent
would leap a quarter of the way along in the first second and then stand still for the length of
the longest call. For the same reason the batched passes report each reply as it lands rather than
after the whole set has been applied — applying is a fraction of a second at the end of a wait that
can run to minutes.

For document ingestion a unit is one file parsed or one model call made, so parsing a large pack
moves the bar rather than looking like dead time. The total is an estimate: a pack big enough to be
read in parts adds steps as it goes, so the interface says "step 4 of about 12" rather than
implying a precision it does not have.

Document ingestion reads its three question groups in parallel, each image in a submitted diagram
pack is read on its own and in parallel with the others, and parsing several submitted files
(PDF, Word, Excel…) into text also happens in parallel — none of that is affected by
`LLM_MAX_CONCURRENCY` or the per-stage concurrency override, since none of it is chunked the way
the stages above are: there are only ever a few facet groups or a few files in flight at once
regardless of scenario space size.

### What a model call is actually sent

The context document has two readers with different needs, so it has two renderings of the same
record.

**For a person**, it carries the quote, document and page behind every claim. That provenance is
the whole point of the document — the question a reader is answering is "can I believe this" — and
it is also most of its length.

**For a model call**, the provenance is dead weight: it cannot be checked from inside the call, and
every character of it is a character not spent on the substance. So later stages are given a
compact rendering built from the same evidence record — every answer, every specific, everything
the documents did not settle, and the workflow read off the diagrams, with the per-claim citations
left out. On a sixty-page pack that is roughly half the size.

`LLM_MAX_CONTEXT_CHARS` (`ingestion.max_context_chars`, default 400,000) caps how much of that plus
the notes one call carries — about 100,000 tokens against models that hold a million. The
generosity is deliberate: a cap a normal pack exceeds does not catch an outlier, it quietly
degrades every run. Going over it drops **whole sections** from the end and says which ones in the
log, rather than cutting mid-sentence; notes and answers are never what gets dropped.

### Ingestion

`LLM_MAX_CORPUS_CHARS` (`ingestion.max_corpus_chars`, default 2,000,000) decides how much submitted
text goes into one reading call. Four characters to a token puts the default near half a million
tokens, well inside a million-token window. A pack larger than this is split across calls, which
reads worse than reading it whole, so raise it before accepting a split.

`LLM_INGEST_RESOLVE_PASSES` (`ingestion.resolve_passes`, default 2) decides how hard the tool tries
to answer its own questions. Each pass is one more call over the whole corpus, and each question it
settles is one the model owner never has to answer. The last pass also rules on what is left,
against one test: **can the intake be filled in without this?** A question earns a place on the
list only by naming which part of the intake it blocks — a branch whose outcomes are never named
blocks `decisions`, and nothing on that branch can be enumerated. An unstated threshold does not:
knowing that escalation happens is enough to test escalation. Everything else is recorded in the
context document without being put to anybody. Set it to 0 to skip the sweep, which is faster and
asks considerably more.

`LLM_VISION` (`ingestion.vision`, default on) is whether the configured model accepts images
alongside text. Turn it off where the gateway rejects the multimodal request shape: diagram reading
then degrades to asking a person to describe the flow, rather than the run failing.

`LLM_MAX_IMAGE_BYTES` (`ingestion.max_image_bytes`, default 4,000,000) caps a single image. Base64
inflates an image by about a third and gateways cap the request body, so anything larger is refused
with a reason rather than sent and rejected.

### Redaction

`PII_REDACTION` (`redaction.enabled`, default off) turns on the redaction described under
[Documents](#1-documents). Off by default so the tool runs unmodified wherever the internal
pii-redactor package has not been installed.

The remaining keys — `mode`, `replacement_text`, `sensitivity`, `exclude_entities`, `allow`,
`thresholds` — are passed to the redaction engine. See the comments in `tuning.yml` itself.

### The example file

```yaml
# tuning.yml — the shape of it; see the file itself for every key and what it is for.
defaults:      {temperature: 0.3, max_attempts: 4}
tiers:
  judgement:   {max_tokens: 65536, reasoning_effort: high}
  materiality: {max_tokens: 32000, reasoning_effort: medium, max_attempts: 2}
  standard:    {max_tokens: 16000, reasoning_effort: minimal}
concurrency: 4
stages:
  writer:             {batch_size: 8}
  materiality_assess: {batch_size: 10}
  reviewer_assess:    {batch_size: 6}
  reviewer_category:  {batch_size: 20}
  coverage_map:       {batch_size: 5}
ingestion:     {max_corpus_chars: 2000000, resolve_passes: 2, vision: true}
redaction:     {enabled: false, mode: masking}
```

The environment-variable name for any of these follows one rule: a tier field is
`LLM_<TIER>_<FIELD>`, a stage field is `LLM_STAGE_<KEY>_<FIELD>`, and the loose ones are
`LLM_MAX_CONCURRENCY`, `LLM_MAX_CORPUS_CHARS`, `LLM_INGEST_RESOLVE_PASSES`, `LLM_VISION`,
`LLM_MAX_IMAGE_BYTES` and `PII_REDACTION`.

Each tier's values are bound to its model with LangChain's `bind`, so a chain carries its own cap
and effort wherever it is used.
