# Agentic Scenario Generator

Builds an independent test benchmark for a conversational AI agent.

You give it the documentation a modelling team submitted about their agent. It reads that
documentation, drafts a structured description of the agent, enumerates every distinct route
through it, adds a library of adversarial probes, and produces two workbooks: a **challenge pack**
you issue to the team, and a **registry** you keep.

The challenge pack contains no expected outcomes. That separation is the point of the exercise.

---

## Contents

- [Installing](#installing)
- [Your first run](#your-first-run)
- [The stages](#the-stages)
- [The intake workbook](#the-intake-workbook)
- [What you get](#what-you-get)
- [Command reference](#command-reference)
- [Configuration](#configuration)
- [Extending](#extending)
- [Testing](#testing)
- [Project layout](#project-layout)

---

## Installing

**Python 3.12.** SafeChain requires it. There is no build step and nothing to add to your path.

Do this **once**. Updating afterwards is a `git pull` — see below.

**Windows**

```
git clone <repository-url> scenario-generator
cd scenario-generator
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py -m pip install -e .
py -m scenario_generator --help
```

**macOS and Linux**

```bash
git clone <repository-url> scenario-generator
cd scenario-generator
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
python3 -m scenario_generator --help
```

Substitute `py` or `python3` for `python` in every command below, to match your platform.

`pip install -e .` installs the package **editable**: the virtual environment points at this
directory rather than holding a copy of it. That is what makes an update a pull and nothing else.

### Updating

```bash
git pull
```

That is the whole of it, in the same directory, with the same virtual environment. Do not clone
again, and do not rebuild the environment — a fresh install per change costs several minutes and
gains nothing.

Three things are deliberately never touched by a pull, because none of them are tracked: your
`.env`, your `config.yml`, and everything under `workspaces/`. A pull cannot lose a run in
progress.

Reinstall dependencies **only when `requirements.txt` itself changes**:

```bash
pip install -r requirements.txt
```

pip skips anything already satisfied, so this is quick and safe to run whenever you are unsure.

**If your machine cannot reach the repository** and the code arrives as a zip instead, do not
extract it over a new directory. Keep one working directory, keep `.venv` inside it, and unpack
the new code over the top — the ignored files listed above are not in the zip, so they survive,
and the environment does not need rebuilding.

### Connecting a model

**SafeChain** does one thing here: given a model name, it returns a LangChain chat model, already
authenticated and already knowing the request body that model expects. Nothing in this package
talks HTTP to a model or holds a token.

Everything after that call is LangChain, and is written to LangChain's documented interfaces
rather than to anything SafeChain adds on top — a wrapper's conveniences change between releases,
the Runnable interface underneath does not. Each call is an ordinary LCEL chain:

```python
prompt | model | StrOutputParser()
```

with each piece doing what its own documentation says: `bind` fixes the generation parameters on
the model, `with_retry` covers a gateway that is busy rather than a request that is wrong, and
`StrOutputParser` turns the reply into text. If you want to know how a call behaves, the
[LangChain documentation](https://python.langchain.com/docs/concepts/lcel/) is the reference —
there is nothing bespoke in between.

You need two things beside the code:

1. **`.env`** — copy `.env.example` and fill in your credentials.

   ```
   CIBIS_CONSUMER_SECRET            From the portal. Paste it exactly as given; the
                                    base64 padding it arrives without is restored for you.
   CIBIS_CONSUMER_INTEGRATION_ID    Your application id.
   CONFIG_PATH                      Path to the YAML below. Defaults to config.yml.
   LLM_MODEL_ID                     Which model to use, named as your config.yml names it.
   ```

2. **`config.yml`** — declares the models available and the request body each one expects. Most
   teams share a template; ask on the SafeChain channel if you do not have one. `LLM_MODEL_ID` and
   the per-tier model variables must name models this file declares.

To check the connection:

```bash
python -c "from scenario_generator.llm.gateway import ask_llm; print(ask_llm('You are terse.', 'Say OK.'))"
```

A missing credential, a model name your `config.yml` does not declare, or a SafeChain that hands
back something other than a LangChain runnable are each reported before any work starts, naming
what is wrong.

If a call fails saying the model factory could not be found, SafeChain is installed but keeps it
somewhere this does not check — the import path has moved between releases. This prints which
interpreter you are on, whether SafeChain is importable from it, and the exact line to add:

```bash
python tools/find_safechain.py
```

Six stages call a model; the rest are deterministic and run without one. Every model-using stage
also accepts `--no-llm`, which substitutes placeholder text — useful for checking an intake before
spending any calls.

---

## Your first run

The interface is the easier way to start.

```bash
python -m scenario_generator serve
```

Open `http://127.0.0.1:5000`, name your use case, and work down the stages on the left. It binds
to localhost only and has no authentication.

Everything it does is also available on the command line:

```bash
# Read what the modelling team sent.
python -m scenario_generator ingest submitted_docs/ acme

# Draft an intake from what was read, then open it and correct it.
python -m scenario_generator draft-intake acme_context.md acme_intake.xlsx

# Build the benchmark and write it up.
python -m scenario_generator generate acme_intake.xlsx acme --with-probes \
    --context acme_context.md

# Review it whole, and rebuild the pack with the result.
python -m scenario_generator review acme_intake.xlsx acme_registry.xlsx acme_registry.xlsx \
    --context acme_context.md --pack acme_challenge_pack.xlsx
```

**If you already have a completed intake workbook**, skip the first two commands. Upload it at
the intake stage, or pass it straight to `generate`.

---

## The stages

| # | Stage | Input | Output |
|---|---|---|---|
| 1 | Documents | The submitted pack: model documentation, the transcripts of their own testing, workflow diagrams, supporting material | A cited context document, and answers to eleven questions about the agent |
| 2 | Intake | A drafted or completed intake workbook | The agent as a decision graph, confirmed by you |
| 3 | Benchmark | The intake | Every distinct route through the graph, plus applicable probes |
| 4 | Scenario text | The benchmark | A description and tester script per scenario |
| 5 | Materiality | The benchmark | A Low / Medium / High / Critical tier per scenario, driving run counts |
| 6 | Final review | The whole benchmark | Settled materiality, checked categories, flagged weaknesses, proposed additions |
| 7 | Coverage | The transcripts of their own testing | How many of their conversations land on each scenario, and which scenarios nothing of theirs reaches |
| 8 | Issue | The registry | The challenge pack to send, and the registry to keep |

Stages 1 and 7 are optional. Skip 1 if you already have an intake; skip 7 if the team submitted no
testing of their own.

**Gaps in the declaration are questions addressed to the row that needs them, not to the
documents.** There used to be a separate "Open questions" stage asking about eleven broad facets
of the evidence -- "what are the known risk areas?" -- which said which section of the documents
was thin but never which decision, state, capability, tool or persona actually needed filling in.
That is folded into the Intake stage now: once a workbook exists, drafted or uploaded, it is read
for where it is structurally thin -- a decision with fewer than two outcomes, a capability with no
type, a terminal state with no outcome type, a state nothing reaches, a persona with no stated
objective -- and each gap becomes a question addressed to that specific row (see
`core/gaps.py`). What the drafter itself flagged as inferred rather than read is folded in beside
them, read back from the workbook's own "Review This" sheet. Anything genuinely cross-cutting --
no state marked as the start, or a question the documents never addressed at all -- is kept, at
the bottom, exactly where it belongs once the row-scoped questions have had first claim on your
attention.

Answering a gap question records a note, the same mechanism notes have always used, and nothing
re-runs automatically. **Revise the intake with these answers**, a separate action on the same
page, is what folds them in: it hands the model the *current* declaration -- drafted, hand-edited,
or both -- alongside every note and answer, old and new, with instructions to change only what the
new information actually requires and carry everything else forward untouched. This is
deliberately not the same call as the first draft, and deliberately not automatic: a plain redraft
has no way to tell your hand correction from something it should re-derive from scratch, and would
silently discard it. Revise as many times as you like; each pass sees everything answered so far.

Stage 1 reads `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.xlsm`, `.csv`, `.md` and `.txt`, and describes
`.png`, `.jpg` and `.jpeg` diagrams with a vision-capable model. Spreadsheets are read sheet by
sheet with the header repeated on each passage, because a rules table is where the thresholds
usually live. A scanned PDF has no text layer and is reported as unreadable rather than read as
empty — ask for a text-based copy.

**Workflow diagrams are read as graphs, not as prose.** A diagram *is* the intake's decision and
state sheets — a box with branching arrows is a decision, an arrow's label is an outcome, the box
it lands in is a state — so the reading keeps that structure the whole way rather than flattening
it to sentences and asking a later pass to rebuild a graph from the sentences. Three passes:

1. **Each image on its own**, into an explicit list of boxes and arrows. Enumerated rather than
   described, because a box missed in a paragraph vanishes silently, where a box missed in a node
   list shows up as an arrow pointing at nothing. Arrows that run off the edge of the image are
   recorded as exactly that — they are where one image joins another.
2. **All the readings together**, joined into one graph and written in the intake's own
   vocabulary: decisions with named outcomes, states with `reached_via`, terminal flags and
   outcome types.
3. **Whatever that graph cannot account for, back to the images.** The graph is checked against
   properties it must have to be walkable at all — every outcome leads somewhere, every state is
   reached by an outcome that exists, a branch has more than one branch — and the specific
   failures are named back to the model with the images still attached: *"DEC-03 outcome Timeout
   leads nowhere; follow that arrow"*. Once, not until clean.

The resulting graph is carried through to the intake drafter **as structure**, alongside the prose
context, so drafting becomes confirming and completing a graph rather than rebuilding one from
sentences about it. It also appears in the context document, so you can read what was extracted.

An image that fails on its own is dropped rather than losing the rest; the diagrams are only
reported unreadable if every one of them was, or if nothing could be made of them together. A
repair pass that returns nothing usable leaves the first reading exactly as it was — a second look
can improve a reading, never damage it.

Every submitted document is named in the reading prompt and the result says which ones any answer
actually rested on. A file that informed nothing is reported: it is either irrelevant or it was
passed over, and those need different responses.

**Redaction.** With `PII_REDACTION=on` (see Configuration), every passage a document is read into
is redacted before it is joined into a corpus and before any of it reaches a model call — names,
account numbers and other business-sensitive material never leave your machine unredacted. This
is not a best-effort pass: if the redaction engine cannot be reached, ingestion stops rather than
sending the document through unredacted.

A **Redact** checkbox next to each uploaded model-documentation or supporting file marks that one
file to be redacted the next time Documents runs, whether or not `PII_REDACTION` is on for
everything else — one particularly sensitive upload does not require switching redaction on for
the whole pack. It has no effect the other way round: there is no per-file opt-out once
`PII_REDACTION` is on. Diagrams have no text to redact, and their own scenario library never
reaches this reading at all (see below), so neither shows the checkbox.

Each stage states how its output was produced — **computed**, **model judgement**, or **human
decision** — because a materiality tier and a graph walk do not deserve the same trust.

Changing an earlier stage marks the later ones out of date rather than leaving them looking
finished. Their output is kept and stays downloadable. Two ways to clear a stage: *Clear this
stage and everything after it* forgets the statuses and keeps the workbooks, for comparing a
rerun against what came before; *Start again from here* deletes what those stages produced.
Neither touches submitted documents or an intake workbook you provided.

**Stopping a run.** Documents, scenario text, materiality and final review can each be dozens of
model calls, so each of them shows a *Stop* button while running. Pressing it stops the run from
sending any further calls — whichever one is already in flight is left to finish rather than cut
off — and nothing that run would have produced is written, so the stage lands back exactly where
it was before you ran it, ready to run again rather than stuck looking failed.

**Stage 2** lists only what blocks the intake, most blocking first, each saying which part it
blocks. Answer as many as you can and press Save once; blanks stay open, and an answer already
given can be changed. Anything the documents left open that does not block the intake sits behind
a toggle. The triage judging what "blocks the intake" means is deliberately conservative about
what counts: a scenario needs to know that a branch exists and where it leads, not the exact
value, threshold or wording that selects it, so an unstated dollar figure or timeout is not put to
you as long as the branch it governs is already named. Statements read off a diagram — which can
run to dozens for one workflow image — are grouped into one confirmation per part of the intake
rather than one per statement, so confirming them is one read of a short list rather than dozens
of near-identical rows.

**Stage 3** draws the graph: **boxes are decisions**, **arrows are the states between them**,
labelled with the outcome that took them there. Zoom, drag to pan, hover for detail. Below it,
what the declaration leaves out — a branch with one outcome, an outcome leading nowhere, a state
nothing reaches — each said as something you can go and fix.

A retry — a decision whose failed outcome leads back to itself or an earlier point — is drawn as a
dashed loop through a lane on the right rather than a straight arrow back up through the rows in
between. That is usually most of what used to make a branchy graph look tangled: an ordinary
top-to-bottom flow with every retry routed the same way it happens (Max Attempts almost always
means a loop) rather than crossing back over everything drawn since.

You can also try a change before committing it. Add a decision or a state and it appears in the
graph immediately, dashed; anything not yet connected sits in a row underneath until you give it
somewhere to go. Press *Write these into the intake* and the rows are appended to the workbook —
everything already in it, including your own edits, is left alone. For changes to what is already
declared, edit the workbook and upload it again.

From stage 5 onward the page shows the benchmark itself: what each scenario asks the agent to do,
what it is judged to be worth and why, and anything the review flagged. Materiality can be
overridden and a flag dismissed from there, straight into the registry. Routes, expected outcomes
and per-turn detail stay in the workbook — the screen carries the reading, the workbook carries
the record.

Any stage accepts free-text notes and extra files. Both are passed to every stage that follows.
On the command line this is `--note`, which is repeatable:

```bash
python -m scenario_generator review intake.xlsx registry.xlsx registry.xlsx \
    --note "Disputes over 500 always go to a person." \
    --note "The vendor document is a version behind."
```

---

## The intake workbook

The intake is the authoritative description of the agent, and the boundary of what can be
tested. Anything absent from it is absent from the benchmark.

| Sheet | Holds |
|---|---|
| `Guide` | Field-by-field instructions with examples |
| `L1 Use Case` | Name, objective, agent type, channel, handoff triggers, safety requirements, success criteria |
| `Personas` | The kinds of user the agent serves |
| `L2 Capabilities` | Each distinct thing the agent can do, typed |
| `L3 Decisions` | Branch points and their named outcomes |
| `L4 States` | Positions the interaction can occupy |
| `Tools` | Systems the agent calls |

Four fields carry more weight than their size suggests.

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
directly; either way it takes hold the next time the benchmark is built.

A drafted intake includes a **Review This** sheet giving a confidence per section and the specific
points the draft could not settle. Read it before relying on the draft.

### Tidying the graph

An optional action on the intake stage, once a workbook is in place: **Look for tidying
opportunities** sends the whole declared graph to a model in one call and asks for two kinds of
proposal, shown on the page rather than written anywhere until you act on one.

**Reconnections** — a decision or state that does not connect to the rest of the graph, with a
specific fix: which state a stray decision should be reached from, or which decision outcome a
stray state should be reached via.

**Consolidations** — decisions that look like alternate routes to the same fact rather than
genuinely different branches, the way a caller might be identified by the last four digits of an
SSN, the full SSN, or a card number, with nothing afterwards depending on which one was used.
Separately, every one of those multiplies the benchmark by every combination without testing
anything additional past the point where they converge; merged into one decision, the same
downstream behaviour is tested at a fraction of the cost. The pass is told which decisions are
*structural* candidates — every outcome of each lands on the same downstream point as every
outcome of the others — as a computed hint, not a verdict; whether merging is actually a good idea
is still its judgement to make, weighing what the capabilities involved are for and how central
the decision is to the use case.

Applying either kind writes straight to the workbook — the same narrow, immediate edit as the
scope toggle above, not a sketch waiting on a separate commit — and invalidates everything after
the intake stage the same way a corrected upload would. A consolidation's merge keeps every
capability the merged decisions used as a note against the new one, since the workbook's
Triggering Capability column holds only one id; the rest survive as text a person can see rather
than as a structural link. Dismissing a proposal discards it without touching anything.

---

## What you get

### The scenario list, on screen

Every scenario-bearing stage (text, materiality, review, coverage, issue) shows the benchmark as
cards rather than a spreadsheet — see `webapp/scenarios.py`. Three view tabs narrow to a starting
point (**Needs attention** — anything flagged, proposed, re-categorised or covered by the owner's
own testing; **Critical and high**; **All**), and dropdown filters narrow further within whichever
tab is active: by category, materiality, origin, persona, review flag or coverage status, whichever
of those the current stage has actually produced. A dropdown only appears once there is more than
one value to choose between — a benchmark with one persona never offers a persona filter that could
only ever select everything.

50 rows render at a time by default; the **Show** control raises that to 100, 250 or all of them.
The page always states how many matched the view and filters versus how many are actually
rendered, so a shorter list always says whether that is because nothing else matched or because
the rest is one click away.

### Challenge pack — issued to the modelling team

Five sheets: `Instructions`, `Scenarios`, `Turn_Plan`, `Run_Log`, `Run_Summary`.

`Run_Log` is pre-populated to the exact number of runs required, so the workload is a fixed
request rather than something the team has to construct. Read in order it forms the transcript.

**It contains no expected outcomes** — no decision path, no expected tool call, no category, no
materiality. A test asserts this on every build.

### Registry — kept by you

`Scenario_Metadata` (full metadata and expected outcome), `Turn_Metadata` (expected outcome per
turn), `Scenario_Text` (the description and script as issued).

### Context document and evidence record

Written by the documents stage. The documents are read whole — every submitted file is turned
into one corpus and put to the model in a few calls, each answering a group of related questions
across all of it at once. Questions the first reading leaves open are put back to the documents
once more before they reach you, so what remains is genuinely absent from the pack rather than
merely missed.

The context document is organised as answers to the eleven questions, each citing verbatim quotes
checked against the source. The evidence record is the machine-readable form of the same thing.

### Coverage report

Three sheets. `Scenarios` is the answer: every benchmark scenario with how many of their
conversations landed on it, least covered first, because the thin end of that list is what goes
back to them. `Conversations` is the working — one row per transcript with the scenario it was
matched to, how sure the match was, what the user wanted and how the exchange ended — so a figure
on the first sheet can be traced to the conversations behind it. `Their grouping` appears only
where they supplied one, and says whether it agrees with ours.

**What the stage reads is transcripts, not a list of scenario titles.** A list of titles is a
claim about their testing; the transcripts are the testing. Teams that have grouped their
conversations under labels of their own are the exception rather than the rule, and requiring that
grouping would put the measurement out of reach of exactly the submissions that need it. Any
grouping they do supply is carried through and assessed against where the conversations actually
landed — a group that splits across several scenarios is a disagreement worth someone's attention.

Three layouts are read, and which one was used is reported back to you: one row per turn with a
conversation id repeated down the sheet; one row per conversation with the whole transcript in a
cell; or a document with no table at all, conversations separated by headings with `User:` /
`Agent:` prefixes on each line. Spreadsheets, CSV, Word, PDF and plain text all work.

**One conversation maps to exactly one scenario, and the match is decided by where the
conversation ends.** A benchmark scenario is a complete route to a specific ending, and some
routes are prefixes of others: a conversation that authenticates and stops is not the same test as
one that authenticates and then goes on to verify a charge, even though the second contains the
first. Filing the short one under the long one would report coverage that does not exist, so the
matcher is told explicitly not to. "No scenario fits" is a first-class answer — it means either
they are testing something the benchmark never enumerated, which is worth knowing, or that
conversation is not a test of this agent.

**Coverage is a count, not a flag.** One conversation against a scenario and forty against it are
not the same evidence. *Represented* is a line drawn through the counts at a threshold you set on
the stage page; changing it recounts what is already mapped and rewrites the workbook, without
another pass over the transcripts. How much evidence is enough depends on how far their testing is
trusted, which is a judgement the tool has no basis for making.

The matcher's confidence in each mapping is shown beside the count, never folded into it. A
scenario with five low-confidence mappings and one with five high-confidence mappings both have
five, and which of those is convincing is exactly the sort of thing worth putting in front of a
person.

Each scenario is also annotated in the registry, under `Their Coverage`, with the count and the
conversation ids behind it. By default that is an annotation and nothing more — no scenario is
dropped, because whether running something they have already tested is duplicated effort or
independent confirmation is your call rather than the tool's. The Issue stage offers a setting
that narrows the pack to what they under-cover; it is off unless you turn it on, since leaving a
scenario out says their evidence for it is accepted. The annotation never appears in the challenge
pack either way — telling the modelling team which scenarios you already consider answered would
tell them exactly which ones to concentrate on.

---

## Command reference

```
ingest              SOURCES... OUTPUT_PREFIX
draft-intake        CONTEXT_FILE OUTPUT [--evidence FILE] [--note TEXT]
revise-intake       CURRENT_WORKBOOK OUTPUT [--context FILE] [--evidence FILE] [--note TEXT]
init-template       OUTPUT
build-graph         INTAKE GRAPH_OUTPUT [--with-probes]
build-probes        INTAKE GRAPH_INPUT GRAPH_OUTPUT
refine              INTAKE GRAPH_INPUT OUTPUT_PREFIX [--no-llm] [--context FILE] [--note TEXT]
assess-materiality  INTAKE REGISTRY_IN REGISTRY_OUT [--no-llm] [--context FILE] [--note TEXT]
review              INTAKE REGISTRY_IN REGISTRY_OUT [--no-llm] [--context FILE] [--note TEXT]
                                                    [--max-proposals N] [--pack FILE]
                                                    [--owner-scenarios FILE]
build-pack          INTAKE REGISTRY PACK_OUTPUT
generate            INTAKE OUTPUT_PREFIX [--no-llm] [--with-probes] [--context FILE] [--note TEXT]
map-coverage        INTAKE REGISTRY THEIR_CONVERSATIONS REPORT [--threshold N]
serve               [--port N] [--workspaces DIR]
```

`ingest` accepts files or directories, and reads a directory one level deep. `generate` runs
build-graph, refine and assess-materiality in one pass.

Work started in the interface is stored under `workspaces/`, one directory per use case, holding
the same files these commands produce. A use case can move between the two freely.

---

## Configuration

Calls run on one of four tiers, because the work genuinely differs. Each tier takes its own model,
output cap, reasoning effort and retry count.

| Tier | Used by | Output cap | Reasoning | Retries | Model |
|---|---|---|---|---|---|
| **Judgement** | reading documents, drafting the intake, review | 65,536 | high | 4 | `LLM_JUDGEMENT_MODEL_ID` |
| **Materiality** | weighing each scenario against its peers | 32,000 | medium | 2 | `LLM_MATERIALITY_MODEL_ID` |
| **Standard** | writing scenario text | 16,000 | minimal | 4 | `LLM_MODEL_ID` |
| **Fast** | mapping their scenarios onto the intake vocabulary | 8,000 | minimal | 4 | `LLM_FAST_MODEL_ID` |

Every tier falls back to `LLM_MODEL_ID`, so nothing changes until you name a smaller model.
Pointing the fast tier somewhere cheap is the first saving worth making: that pass is
classification against a closed list, and anything outside the list is discarded by validation
regardless.

Materiality is split out from judgement rather than sharing it, because it is also the tier under
the most concurrent load: every chunk of the benchmark is sent at once (see Batching below), so a
gateway hiccup there means several simultaneous retries rather than one. It defaults to a shorter
retry ladder for that reason, and can be pointed at a smaller model independently of judgement if
you are seeing connection errors under load.

### Per-stage overrides

A tier is shared by every call doing the same *kind* of work, which is coarser than every call
*site*: weighing a scenario's materiality and checking the review's declared category are both
Materiality-tier work, but they are two different calls in two different passes, and a benchmark
can want them tuned differently -- a smaller model for the mechanical category check, the full one
for materiality itself. `LLM_STAGE_<KEY>_*` sets any of `MODEL_ID`, `MAX_TOKENS`,
`TEMPERATURE`, `REASONING_EFFORT`, `MAX_ATTEMPTS` for one specific call site, and `_BATCH_SIZE`
for the ones that batch. Every field falls back to its tier's own setting where the stage does not
override it, which itself falls back to `LLM_MODEL_ID` -- three levels, stage then tier then
master, and setting nothing at this level changes nothing.

| Stage key | Call site | Tier | Default batch |
|---|---|---|---|
| `INGEST_READ` | Reading each facet group from the whole corpus | Judgement | — |
| `INGEST_RESOLVE` | The resolution sweep | Judgement | — |
| `INGEST_DIAGRAM_READ` | Reading one diagram image on its own | Judgement | — |
| `INGEST_DIAGRAM_SYNTHESIZE` | Joining every image's reading into one graph | Judgement | — |
| `INGEST_DIAGRAM_REPAIR` | Putting unresolved points back to the images | Judgement | — |
| `INTAKE_DRAFT` | Drafting the intake from the evidence | Judgement | — |
| `STRUCTURE_REVIEW` | Proposing reconnections/consolidations on the intake | Judgement | — |
| `WRITER` | Writing scenario text | Standard | 8 |
| `MATERIALITY_ASSESS` | Weighing each scenario's materiality | Materiality | 10 |
| `REVIEWER_ASSESS` | Review's materiality + flagging sweep | Judgement | 6 |
| `REVIEWER_CATEGORY` | Review's declared-category check | Materiality | 20 |
| `REVIEWER_PROPOSE` | Review's addition proposals | Judgement | — |
| `COVERAGE_MAP` | Mapping their conversations onto the benchmark | Judgement | 5 |

For example, `LLM_STAGE_REVIEWER_CATEGORY_MODEL_ID=some-cheap-model` moves only the category
check onto a smaller model, leaving materiality assessment on whatever `LLM_MATERIALITY_MODEL_ID`
(or `LLM_MODEL_ID`) says, even though both share the Materiality tier by default.

Settings live in two files, split by who owns the answer.

**`.env`** — yours and your machine's: the SafeChain credentials, which model to call, where
SafeChain is. Never committed, and short enough to read at a glance.

**`tuning.yml`** — how the work is run: the tier table above, per-stage overrides, batch sizes,
concurrency, how hard ingestion tries, redaction. None of it is secret, all of it is worth a team
agreeing once, and as a table it is legible in a way forty `KEY=value` lines are not. It is
committed, so a change to it is reviewed like any other change. Delete a key and the built-in
default applies; delete the file and the tool runs exactly as it ships.

Every setting in `tuning.yml` also has an environment variable name, and the environment always
wins. That is how you override one setting on one machine for one run without editing a shared
file — and it means an existing `.env` full of `LLM_*` settings keeps working untouched.

```yaml
# tuning.yml — the shape of it; see the file itself for every key and what it is for.
defaults:      {temperature: 0.3, max_attempts: 4}
tiers:
  judgement:   {max_tokens: 65536, reasoning_effort: high}
  materiality: {max_tokens: 32000, reasoning_effort: medium, max_attempts: 2}
  standard:    {max_tokens: 16000, reasoning_effort: minimal}
  fast:        {max_tokens: 8000,  reasoning_effort: minimal}
concurrency: 4
stages:
  writer:            {batch_size: 8}
  materiality_assess: {batch_size: 10}
  reviewer_assess:   {batch_size: 6}
  reviewer_category: {batch_size: 20}
ingestion:     {max_corpus_chars: 2000000, resolve_passes: 2, vision: true}
redaction:     {enabled: false, mode: masking}
```

The environment-variable name for any of these is the one documented above: a tier field is
`LLM_<TIER>_<FIELD>`, a stage field is `LLM_STAGE_<KEY>_<FIELD>`, and the loose ones keep the
names they always had (`LLM_MAX_CONCURRENCY`, `LLM_MAX_CORPUS_CHARS`,
`LLM_INGEST_RESOLVE_PASSES`, `LLM_VISION`, `PII_REDACTION`, and so on).

Each tier's values are bound to its model with LangChain's `bind`, so a chain carries its own cap
and effort wherever it is used.

`max_tokens` is the **output** cap, not the context window. Reasoning tokens come out of the same
budget as the reply, so a high reasoning effort against a small cap truncates the JSON rather
than shortening the answer — which is why each tier sets both together.

`LLM_MAX_CORPUS_CHARS` decides how much submitted text goes into one reading call. A pack larger
than this is split across calls, which reads worse than reading it whole, so raise it before
accepting a split.

`LLM_INGEST_RESOLVE_PASSES` decides how hard the tool tries to answer its own questions. Each pass
is one more call over the whole corpus, and each question it settles is one the modelling team
never has to answer. The last pass also rules on what is left, against one test: **can the intake
be filled in without this?** A question earns a place on the list only by naming which part of the
intake it blocks — a branch whose outcomes are never named blocks `decisions`, and nothing on that
branch can be enumerated. An unstated threshold does not: knowing that escalation happens is
enough to test escalation. Everything else is recorded in the context document without being put
to anybody. Set it to 0 to skip the sweep, which is faster and asks considerably more.

Every tier's model name has to be one your `config.yml` declares. Pointing the fast tier at a
smaller model is the first saving worth making: that pass is classification against a closed list,
and anything outside the list is discarded by validation regardless.

**If a reply comes back truncated**, reduce the batch size first. Raise the cap only if that does
not resolve it.

**How many calls a stage made** is reported when it finishes — in the stage's own result panel in
the interface, and on the last line of the command's output on the command line. A count that
jumps between two runs of the same stage is usually the first visible sign that chunking or the
refill path has changed behaviour. Retries inside a call are not counted again: the number is how
much work the stage asked for, not how many times the transport had to ask for it.

### Batching

"Batching" is two separate numbers, and they answer two different questions.

**Batch size** — how many rows go into the payload of *one* call. This decides how many calls a
fixed amount of work turns into: a benchmark of 100 scenarios at a batch size of 10 is 10 calls; at
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
- **Materiality** (10 a call) has to see enough of the benchmark at once to judge relative
  consequence — whether a scenario is "the worst thing here" depends partly on what else is in the
  same call — without the call growing so large that a single scenario's tier gets lost in it.
- **Review's assessment sweep** (6 a call) is the most demanding read per scenario: it is settling
  materiality, flagging redundant or under-specified scenarios, and doing it with the full
  reviewer field guide in view, so the chunk is kept small enough that each scenario still gets a
  considered look rather than a skim.
- **Review's category sweep** (20 a call) is a narrower, more mechanical judgement — does the
  declared outcome type actually match what the scenario does — so it tolerates a much larger
  chunk without the same loss of attention per item.

None of this is tuned to a model's context window; every call here is far short of it. It is tuned
to how much one call can weigh carefully at once, which is a much smaller number.

Both numbers are customisable per call site, the same `LLM_STAGE_<KEY>_*` cascade described under
Per-stage overrides above: `LLM_STAGE_<KEY>_BATCH_SIZE` for rows per call,
`LLM_STAGE_<KEY>_CONCURRENCY` for calls in flight, falling back to `LLM_MAX_CONCURRENCY` where the
stage does not set its own. `LLM_STAGE_REVIEWER_ASSESS_BATCH_SIZE=4` and
`LLM_STAGE_REVIEWER_ASSESS_CONCURRENCY=8`, say, sends smaller, more careful review calls while
keeping more of them in flight at once.

Document ingestion reads its three question groups in parallel, each image in a submitted diagram
pack is read on its own and in parallel with the others, and parsing several submitted files
(PDF, Word, Excel...) into text also happens in parallel — none of that is affected by
`LLM_MAX_CONCURRENCY` or the per-stage concurrency override, since none of it is chunked the way
the stages above are: there are only ever a few facet groups or a few files in flight at once
regardless of benchmark size.

---

## Extending

**Changing a prompt.** Every prompt is a file in `scenario_generator/prompts`, named
`<pass>.<purpose>.md`. Edit the wording and run again — nothing is compiled. Anything in double
braces, like `{{use_case}}`, is filled in at run time; leave those exactly as they are. If one is
renamed or removed, the run stops with a message naming the file and the slot.

**Adding a probe.** Append an entry to `probe_library.yaml` with an id, family, name, intent,
expectation, applicability predicate and turn count. The test suite rejects an entry that names a
domain-specific object or references an unknown predicate.

**Adding an applicability predicate.** Add it to `PREDICATES` in `core/probes.py` as a function of
the intake.

**Adding a document format.** Add a reader to `ingest/readers.py` and one entry to its registry.

**Adjusting run counts.** `RUNS_BY_MATERIALITY` in `core/models.py`.

---

## Testing

```bash
python -m unittest discover -s tests
```

174 tests, standard library only. Beyond unit coverage, several exist to protect properties that
would otherwise fail silently:

- The challenge pack contains no expected outcomes or ground-truth columns.
- The scenario writer is never given a scenario's terminal state, so the expected outcome cannot
  reach the pack through the text it writes.
- A quote that was reworded rather than copied from the source is rejected; one mangled by PDF
  extraction is still matched.
- A document that cannot be read is refused with a reason, and one unreadable file does not stop
  the rest of the pack being read.
- A run where most model calls failed is abandoned rather than written.
- Facts stated in three separate sections are assembled into one answer.
- A submitted pack is read in a handful of model calls rather than one per passage.
- A quote invented from fragments scattered across the pack is rejected; the match must be local.
- A drafted intake produces a working benchmark without being edited.
- Submitted conversations are read from a workbook with unfamiliar headings and a cover sheet in
  front of the data, a semicolon CSV, or a document with no table at all.
- A conversation is matched to the scenario it *ends* on, never to a longer scenario that merely
  contains it, and "nothing fits" is reported rather than forced to a nearest match.
- Coverage counts conversations rather than flagging scenarios, and moving the representation
  threshold recounts without another model call.
- Changing a stage marks every later stage out of date, and out-of-date output is kept.
- The interface uses no Flask API newer than 1.0.
- Each pass runs on the tier its work needs, so a judgement call cannot be quietly demoted.

---

## Project layout

```
scenario_generator/
    core/       Intake parsing, decision graph, scenario generation, probes,
                proposals, representation counting, evidence, grounding.
    ingest/     Document readers, extraction, context assembly, intake
                drafting, submitted-conversation parsing.
    llm/        LangChain chain building, prompt loader, and the passes.
    prompts/    The prompt library, one file per prompt.
    io/         Workbook reading and writing.
    webapp/     The local interface.
    utils/      Text, JSON and batching helpers.
    cli.py      Argument parsing.
    pipeline.py Stage orchestration.
tests/          Test suite.
tools/          Developer utilities.
examples/       A worked intake and the context that accompanies it.
```

To confirm which copy of the package is being imported:

```bash
python -c "import scenario_generator; print(scenario_generator.__file__)"
```
