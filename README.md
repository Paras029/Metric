# METRIC

Builds an independent scenario space for a conversational AI agent.

You give it the documentation a model owner submitted about their agent. It reads that
documentation, drafts a structured description of the agent, enumerates every distinct route
through it, adds a library of adversarial probes, and produces two workbooks: a **data template**
you issue to the model owner, and the **scenario space metadata** you keep.

The data template contains no expected outcomes. That separation is the point of the exercise.

**[HOW_IT_WORKS.md](HOW_IT_WORKS.md)** is the companion document: what each stage does and why,
how documents and diagrams are read, how coverage is measured, and every setting in the tuning
file. This page is setup, launch and the shape of the thing.

---

## How the code is laid out

```
metric/
  domain/     the declaration, the graph it describes, the workbooks both live in
  llm/        model plumbing: gateway, tiers, batching, metering. Knows nothing about scenarios
  shared/     text, JSON and batching helpers
  phases/
    intake/              1 - the agent as a declared decision graph
    scenario_generator/  2 - walk it, write each route up, weigh it, review the set
    variation_generator/ 3 - variants worth running separately (not built)
    coverage/            4 - what the owner already tests, and the pack to issue
    evaluation/          5 - score the transcripts they return (placeholder)
  web/        the local interface; each stage's page is assembled here
```

Each step keeps its prompts beside the code that sends them, in its own `prompts/` directory.
Anything more than one phase needs lives in `domain/` or `shared/` — that rule is what keeps
`graph.py` out of the workflow step even though the workflow step is what walks it. Imports are
absolute throughout, so a module's dependencies read at a glance.

## Contents

- [Installing](#installing)
- [Connecting a model](#connecting-a-model)
- [Your first run](#your-first-run)
- [The stages](#the-stages)
- [What you get](#what-you-get)
- [Command reference](#command-reference)
- [Configuration](#configuration)
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
py -m metric --help
```

**macOS and Linux**

```bash
git clone <repository-url> scenario-generator
cd scenario-generator
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
python3 -m metric --help
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

---

## Connecting a model

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
python -c "from metric.llm.gateway import ask_llm; print(ask_llm('You are terse.', 'Say OK.'))"
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
python -m metric serve
```

Open `http://127.0.0.1:5000`, name your use case, and work down the stages on the left. It binds
to localhost only and has no authentication.

Everything it does is also available on the command line:

```bash
# Read what the model owner sent.
python -m metric ingest submitted_docs/ acme

# Draft an intake from what was read, then open it and correct it.
python -m metric draft-intake acme_context.md acme_intake.xlsx

# Build the scenario space and write it up.
python -m metric generate acme_intake.xlsx acme --with-probes \
    --context acme_context.md

# Review it whole, and rebuild the data template with the result.
python -m metric review acme_intake.xlsx acme_scenario_space_metadata.xlsx acme_scenario_space_metadata.xlsx \
    --context acme_context.md --pack acme_data_template.xlsx
```

**If you already have a completed intake workbook**, skip the first two commands. Upload it at
the intake stage, or pass it straight to `generate`.

Work started in the interface is stored under `workspaces/`, one directory per use case, holding
the same files these commands produce. A use case can move between the two freely.

---

## The stages

| # | Stage | Input | Output |
|---|---|---|---|
| 1 | Intake drafting | The submitted pack — model documentation, workflow diagrams, supporting material — or a completed intake workbook | The agent as a decision graph, drafted from the documentation and confirmed by you |
| 2 | Workflow | The intake | Every distinct route through the graph, walked depth-first, plus applicable probes |
| 3 | Scenario space | The routes | A name, a description and a tester script per scenario |
| 4 | Variation space | The scenario space | The variants of each scenario worth running separately — **not built yet** |
| 5 | Materiality | The space | A Low / Medium / High / Critical tier per scenario, driving variation counts |
| 6 | Review | The whole space, against the documentation | Settled materiality, checked endings, flagged weaknesses, proposed additions including the end-to-end journeys |
| 7 | Coverage | The transcripts of the model owner's testing | How many of those conversations land on each scenario, and which none of them reach |
| 8 | Summary | The scenario space metadata | The data template to send, and what still has to be asked for |

Reading the documentation and drafting the intake are one stage because they are one job: the
reading exists in order to be drafted from, and nothing happens between them that you decide.

**Drafting is three passes, not one.** The draft is written from the documents. Then the
declaration is audited against what the graph must have to be walked at all — a branch with fewer
than two named outcomes, an outcome that leads nowhere, a decision no state routes into — and
whatever the audit found is put back to the documents by name. Then the whole declaration is read
back once: not row by row this time, but as a description of one agent, against the routes it
actually enumerates to. That last pass is the only one that can see what two rows do to each
other — a decision duplicating one three rows above under another name, a retry limit on a
decision nothing loops back into, an escalation state no stated hand-off trigger accounts for.
None of those is a gap in any single row, so nothing before it looks.

All three are non-destructive by construction rather than by instruction. Anything a later pass
drops is put back, and its result is audited against the declaration already on disk and discarded
if it walks worse.
Stages 4 and 7 are optional — the variation space is a placeholder that passes the scenario space
through unchanged, and coverage is skipped where the model owner submitted no testing of their own.

Changing an earlier stage marks the later ones out of date rather than leaving them looking
finished. Their output is kept and stays downloadable. Two ways to clear a stage: *Clear this
stage and everything after it* forgets the statuses and keeps the workbooks, for comparing a
rerun against what came before; *Start again from here* deletes what those stages produced.
Neither touches submitted documents or an intake workbook you provided.

**Removing things.** A stage offers three clears, because there are three intentions. *Clear this
stage and everything after it* forgets the statuses and keeps every file, for comparing a rerun
against what came before. *Start again from here* deletes what those stages produced. *Start from
nothing* also removes what was submitted to them — the documentation, the diagrams, the
transcripts — because a "start again" that leaves the old pack in place produces a next run built
from documents nobody remembers uploading, which for a tool whose output is meant to be traceable
to its inputs is the worst state to be in. It is scoped to the stage: clearing coverage does not
throw away the model documentation the intake was built from.

A whole workspace can be deleted from the entry screen. That and *Start from nothing* are the only
two irreversible actions in the tool, and they are the only two that ask first — a confirmation on
everything is one people learn to click through.

**Stopping a run.** Documents, scenario text, materiality and final review can each be dozens of
model calls, so each of them shows a *Stop* button while running. Pressing it stops the run from
sending any further calls — whichever one is already in flight is left to finish rather than cut
off — and nothing that run would have produced is written, so the stage lands back exactly where
it was before you ran it, ready to run again rather than stuck looking failed.

**The declaration is editable where it is read.** The intake stage carries the whole declaration
— decisions, states, capabilities, tools, personas — behind toggles rather than as five lists down
the page. Any row opens into its fields; edit them and save. The workbook stays the record and a
change made here is a change made there, but the round trip is gone: correcting one outcome used
to mean downloading the workbook, finding the row, editing, saving and uploading, and the
judgement being made in that loop is made by looking at the graph, which is on screen the whole
time.

Three things the panel does that a spreadsheet cannot:

- **Selecting a row lights it in the drawing**, and what lights differs by kind. A decision is a
  box. A state is usually an *arrow* — only an ending has a box of its own. A capability is every
  decision tagged with it, and its block in the collapsed view. A tool is the decisions of the
  capability it belongs to, which is the honest answer to "where is this used". A persona lights
  nothing, and the panel says so rather than looking broken: every route is walked by every
  persona.
- **Edits are staged and can be previewed.** *Show it in the graph* applies them to a copy of the
  workbook and redraws from that, so a new decision can be seen attaching before anything is
  written. Nothing is saved until *Save changes*, which marks every stage built from the
  declaration out of date — but not the intake itself, which is current by definition: the
  declaration *is* the workbook, and the workbook has just been written. Its counts are
  recomputed rather than marked stale.
- **Adding a row creates it.** The next free id is filled in already; *Add* writes the row and
  reopens it ready to fill in. Staging an addition instead would be an intention with nothing on
  screen to show for it.
- **Ids can be changed, and the change is carried.** Renaming `DEC-03` to `DEC-30` repoints every
  reference to it — the states that offer it, the states it routes to, the spans it sits in, the
  tools tagged with it — in the same save. Ids get typed in a hurry and read for the rest of the
  exercise, and a rename that left the references behind would be worse than no rename at all. It
  is refused only when the new id is already taken. This is the one edit that *does* cascade, and
  deliberately: a rename leaves nothing undefined, it is bookkeeping, whereas a deletion leaves
  genuine holes that somebody has to decide about.
- **The use case is a row like any other.** It leads the toggles, and it is the only one with no
  id, no *Add* and no *Remove* — there is exactly one use case, and the panel says that by what it
  does not offer rather than by refusing afterwards.
- **Saving keeps you where you were** — the same view, the same tab, the same row open.
- **Removing is staged too**, so what stops being reachable can be seen before the row goes. A
  deletion is never cascaded: anything still pointing at what was removed is reported, and the
  audit raises it on the next render, because cleaning those up as well turns one deliberate
  removal into several nobody asked for.

Everything here works with scripting off — each row is an ordinary form that posts and reloads.
The staging, the preview and the highlighting are what scripting adds.

**Building a capability.** Three steps in one order, because each answers from the one before it.

1. **Tick its decisions.** Every declared decision is listed, with the capability it currently
   belongs to. A decision belongs to exactly one, so ticking it moves it — which is the edit
   actually needed most of the time, and one a list of "this capability's decisions" cannot
   express.
2. **See what they fold in.** The states those decisions are offered by and route to, listed as
   the ticking happens.
3. **Pick the boundary from those.** *Entered at* offers the states that offer its decisions;
   *hands on or ends at* offers the states they route to. Both say on what basis they were drawn,
   and both keep **Every state** one click behind them — a span may legitimately begin or end
   anywhere, and a control offering only what it guessed at is a span nobody can correct.

Ticking anywhere in either list rewrites the line above the row, so the span reads back as it is
being drawn — and it says which half is still missing rather than only that the span is not
finished. "No exit yet" is something to act on; "no span" printed over a state you have just
ticked reads as the tick not having registered, which is exactly what it was taken to mean.

The two lists fail differently, and that is worth knowing. *Entered at* is drawn from the states
that **offer** a capability's decisions, and a decision no state offers — an orphan, which a first
draft produces routinely — contributes nothing to it while contributing its full share to the
exits. That left an empty entry control beside a full exit one, which reads as the entry being
broken rather than as the graph being incomplete. So the entry list keeps whatever is already
drawn, and where there is still nothing to propose it falls back to where the conversation starts
and says why. A span has to be entered somewhere, and the opening is always a legitimate answer.

The order matters and getting it wrong is what made this unusable before. Membership is recorded
on the *decisions*, which is the right place for it — a decision belongs to one capability and the
workbook says so where the decision is. It was the wrong place to *edit* from: the capability's
boundary controls were computed from its membership while its own row could not change that
membership, so a wrong grouping could be seen and not corrected, and every list derived from it
was wrong in the same way.

Drawing the first span on a declaration that had none makes the collapsed view appear.

Each capability reads as what it is — the states it is entered
at, an arrow, the states it hands on or ends at. *Change the span* opens two lists, each cut to
the states that could plausibly be a boundary of *that* capability and each saying on what basis
it was cut; the full graph is never more than a save away, and anything already drawn stays in
the list whether or not the shortlist would have suggested it.

Where a block **starts** is a judgement about the agent, and nothing proposes it. Where it **ends**
is arithmetic: walk what the block holds from that start, and every state one of its decisions
lands on that the block is no longer inside is a way out. *Take the endings from the graph* fills
those in, and it routinely finds endings nobody listed — a block hands on where somebody wrote it
down and also refuses, escalates and locks out, and those endings are what the pack tests. It is
offered on a button rather than applied on save, because two real cases derive badly: a block
whose decisions are reachable from outside it, and a block deliberately drawn to stop early.

**Ids are whatever the declaration calls them.** `S-01` and `DEC-04` are what this tool writes,
and a workbook is free to name its states `S-START`, `DEC-VERIFY` or `WELCOME` instead. Cells that
list ids — a capability's span, a state's next decisions — are *scraped* rather than split, so
"S-00, S-03" and "S-00 and S-03" and a list down the cell all mean the same thing; what they are
scraped for is the ids the workbook actually declares, and only then anything of the right general
shape. A reference to a state that does not exist is still read, and reported as dangling, rather
than disappearing on the way in.

**Reading the declared graph.** Boxes are decisions, arrows are the states between them, rounded
boxes are the ways an interaction can end. Hovering anything holds that one thread at full strength
and drops the rest back, which answers "where does this come from and where does it go" without
tracing it with a finger.

Folding the capabilities redraws the same graph one level up, and a capability is not drawn as a
bigger decision. It carries a **doubled outline** and says how many decisions are inside it, which
is how a statechart has always distinguished a composite state from a plain one. Shape is the
graph's language — a stadium ends the interaction, a square decides something, a doubled box holds
a graph of its own — so the collapsed view is readable as a different picture rather than as the
same picture with fewer boxes, and nobody has to remember which level they are looking at.

**Routes that go round a loop are not enumerated, and that is a choice.** *Max Attempts* on a
decision says how often the agent may retry it on the spot — three goes at an identity check —
and the walk honours that. A **return** is different: the flow genuinely going elsewhere and
coming back, as when a fallback that could not understand the request routes to the start of the
block. The intake says nothing about how often that may happen, so any bound on it would be this
tool's invention rather than the declaration's; and walking them multiplies the space by every
loop in the graph to test the same behaviour a second time from a different distance. That is a
*variation* on a scenario rather than a scenario, and it belongs in the variation space where the
number of laps is a knob. The cost is real and worth stating: an outcome whose only way onward is
round a loop is not in the base space at all.

**The backstops are derived, not chosen.** Every decision can fire a bounded number of times on
one route, so the longest route a declaration allows is arithmetic over that declaration — and the
depth limit is read off the arithmetic rather than set to a number that was under it. A limit
derived this way cannot cut a route the rules would have allowed, which is the only thing a depth
limit was ever wanted for: guaranteeing the walk ends. The cap on how many routes one run may
produce stays a constant, far above anything a real declaration reaches, because an interface that
hangs is worse than one that says it stopped — and when it does stop, the stage says so.

**It says what the walk could not reach.** An ending a capability's own decisions can produce
that no route through it arrives at is named beside the drawing, with the reason — because a pack
missing the ending nobody could get to looks exactly like a complete one, and the three reasons
want three different fixes:

- **the decision that produces it cannot be reached from where the block is entered** — the
  declaration is incomplete, or the block is entered somewhere other than where it starts;
- **the block is declared to hand on before it gets there** — the span is drawn too narrow: an
  exit sits on the way, and the walk stops at an exit by definition;
- **the route to it is longer than the depth backstop** — nothing is wrong with the declaration,
  and the enumeration is genuinely incomplete.

Answered by reachability rather than by walking, so it costs two searches per block and is on the
page as the declaration is edited rather than only after a run. An ending only reachable past a
retry limit is not reported: that one *is* reached, by the augmentation pass that exists for it.

**The drawing answers where the material work is.** A materiality table says how material each
scenario is. It does not say *where* the work that matters comes from — which branch of the agent
produces the scenarios worth running and which produces thirty that are not — and no sorting of
three hundred rows says it either, because that is a question about the shape of the graph. So
from the materiality stage on, *Show volume* offers the drawing a second reading, **one band at a
time**:

- **Off** leads the row, because a reading laid over the drawing has to be removable and the
  control should say so rather than leaving somebody to work out which option means none.
- **All** — every scenario, in the interface's own blue. Volume is not a judgement, so it does not
  take a hue that would compete with the tiers, which are.
- **High**, **Medium**, **Low** — the same reading over one tier, in that tier's colour. "Where do
  the High-materiality scenarios come from" is a question with an answer; an arrow coloured by
  whichever tier happened to dominate it is a summary of three numbers that hides all three and
  leaves the reader doing arithmetic against a legend.
- **Review changes**, on the review stage only — green where the review proposes something new,
  amber where it flags what is already there, and nothing at all where it has no opinion, because
  a picture where everything is coloured says nothing about where the change is.

*Without the flagged ones* recomputes whichever band is showing with the flagged scenarios taken
out, against the same scale, so the thinning across the graph **is** the review's recommendation
rather than a list of ids to read against the picture. Each band is counted twice for that reason:
taking the flagged scenarios out of the High band is a different subtraction from taking them out
of the space.

Only the arrows are painted, and never the boxes. A box's colour already means something — which
kind of ending it is, whether it is out of scope, whether it is a block — and that reading is true
of the *declaration*, where a paint is true of one run over it; overwriting the first with the
second loses a fact to show a number. The number has somewhere of its own to go: a channel behind
each arrow, widening with what flows along it, leaving the line itself untouched.

Counted over the same routes the card highlighting uses, so an arrow a scenario lights when its
card is opened is an arrow it is counted on. The collapsed view is painted the same way, so
folding the capabilities to see the shape of the agent keeps the reading; because every scenario
is scoped to one block, a hand-off carries the routes that *end* by handing on along it. The
scenario list stays beside it, so a tier worth arguing with is one click from the route it was
assigned to.

**Opening a scenario lights the route it walks.** A scenario *is* a route through this graph, and
the graph is drawn on every stage that lists scenarios for exactly that reason. Open a card and its
path lights up from the start box to the ending; open several and all of them stay lit, which is
how two scenarios are compared — not by reading both descriptions and holding the difference in
your head, but by seeing where the two paths part. Collapse a card and its route goes back down.
*Clear routes*, or Escape, closes them all. The graph starts folded away on those stages and
unfolds itself the first time you open a card.

**The graph gets the window when it needs it.** *Open full screen* on the graph's own header
takes it out of the page into a view that fills the screen — the same drawing, the same zoom, the
same lit routes, because the section is moved rather than redrawn. On a stage that has a scenario
space the button reads *Open with the scenarios* and the list comes with it: the list down one
side, the drawing down the other, and opening a card lights its route without anything scrolling.
That is the point of the highlighting and it never quite paid off while the two were several
screens apart. Escape, or *Close*, puts both back where they were with the open cards still open.

The graph also leaves the tool. *Open the graph on its own* and *Download it as a page that still
zooms* both produce the same self-contained file — same drawing, same zoom, same highlighting, and
the scenario list with it, so opening a scenario lights its route offline exactly as it does here.
The stylesheet and the script are inlined, so it reads from a mail attachment or a share with
nothing running. A graph big enough to need a zoom is a graph a screenshot cannot carry.

**It is built from the workbooks, so a corrected sheet gives a corrected page.** On the command
line that is one command, which is the point of it being modular rather than a picture that was
true once:

```bash
python -m metric graph-page acme_intake.xlsx acme_graph.html \
    --scenarios acme_scenario_space_metadata.xlsx
```

The intake alone gives the graph; adding the scenario space adds the list. Neither needs a
workspace, and the same function builds the page here and in the interface, so the file and the
screen cannot show different pictures of one declaration.

**What the declaration still needs.** The intake stage lists what would stop part of the graph
being built, addressed to the row that needs it: a decision that names no outcomes, a state nothing
reaches, a capability with no type. Nothing else is asked. What the documents left open about
policy, testing or vocabulary is in the context document, where it is a remark on how complete the
pack is rather than a task with your name on it.

Answers are saved together, blanks stay open, and each is recorded against its question — so a
re-run of the stage reads it as an answer rather than as a loose remark, and folds it into the
declaration without anything being re-typed into the workbook.

Any stage accepts free-text notes and extra files. Both are passed to every stage that follows.
On the command line this is `--note`, which is repeatable:

```bash
python -m metric review intake.xlsx scenario_space_metadata.xlsx scenario_space_metadata.xlsx \
    --note "Disputes over 500 always go to a person." \
    --note "The vendor document is a version behind."
```

**Every scenario carries a short name** — a handle rather than a summary, about the situation
rather than the expected behaviour. It leads the `Scenarios` sheet of the data template, sits beside the id
in the scenario space metadata, and is the title of every row on screen. A scenario space of three hundred rows whose
only handle was the first sentence of each description was not scannable, because those sentences
all open the same way.

**What the writer produces is audited before you see it.** A name, a description that is one, a
turn plan with lines in it — and whether the text carries a distinctive phrase out of that
scenario's own ending. The writer is never shown where a route finishes, but it is shown the
outcome of every step on the way, so a route ending in a lockout can be written up as "and the
account is locked" with nothing having told it so. That text is issued to the model owner, and a
pack that states the answer measures nothing. Anything that fails goes back once with the fault
named; anything still failing is logged by id.

**[HOW_IT_WORKS.md](HOW_IT_WORKS.md#the-stages-in-detail)** covers each stage properly: how the
documents are read, how a workflow diagram becomes a decision graph, what the intake workbook's
load-bearing fields do, and how coverage is measured.

---

**What is issued has to be runnable by somebody who has never seen the agent.** That is the one
property the scenario text has to have, and it fails quietly: a pack of scenarios nobody can run
consistently looks exactly like a pack of good ones. Two testers reading "provide the relevant
details" run two different tests, and transcripts that cannot be compared are the failure the
whole exercise exists to avoid.

It is checked in two places, split by what each can actually decide. **Code checks the facts**: a
turn plan with fewer lines than the route has steps, a line too short to act on, a phrase that
stands in for the thing it should have named, a bracket left for somebody to fill in. Each names
the fault rather than asking for a better attempt, and each one that fires puts that scenario —
only that scenario, and only once — back to the model with its faults listed. **The review
judges the rest**, and says what is missing rather than that something is: "turn 2 says to give
the account details without saying which" is something somebody can act on, where "the wording is
unclear" is not.

Where the line between the two sits matters, because moving it the wrong way is expensive. "This
description is one sentence where the prompt asked for two" is a fact, and it is deliberately not
checked by code: a single sentence naming the position, the condition and the subject matter is a
good description, so the check would fire on sound text and buy a repair call per scenario to
change nothing.

**Nothing runs end to end, and the review is what fixes that.** Scoping by capability means a
verification scenario starts with the cardmember already identified. The routes that cross a
boundary are therefore exactly the ones nobody can see by reading the space — so the review is
given the chain of blocks and asked for the important full journeys: a detail established in one
block that a later one relies on, three failed attempts spread across two blocks where each is
within its own limit, a route that goes into an earlier block and comes back, and exactly one
end-to-end success as the reference conversation. Capped at three, because a pack of journeys is
the scenario space this design exists to avoid. They are marked **End to end** in the list and run
from the beginning rather than seeded part way through.

**Out of scope is a boundary, not a dead end.** Marking a decision out of scope says "do not test
past here" — a sub-system reviewed under a separate engagement. Routes arriving at it now finish
there and stay in the pack, with the hand-off stated in the expected ending, so everything leading
up to the boundary is still tested. It used to mean "do not test anything that leads here", which
is a much more expensive statement than anybody intended.

**Routes are listed before probes.** A probe tests what the agent must refuse whatever route it is
on, so it belongs in the pack but not at the top of it.

## What you get

**Data template** — issued to the model owner. Five sheets: `Instructions`, `Scenarios`,
`Turn_Plan`, `Variation_Log`, `Variation_Summary`. `Variation_Log` is pre-populated to the exact number of variations
required, so the workload is a fixed request rather than something the model owner has to
construct; read in order it forms the transcript. **It contains no expected outcomes** — no
decision path, no expected tool call, no category, no materiality. A test asserts this on every
build.

**Scenario space metadata** — kept by you. `Scenario_Metadata` (full metadata and expected outcome),
`Turn_Metadata` (expected outcome per turn), `Scenario_Text` (the description and script as
issued).

**Context document and evidence record** — written by the documents stage. The context document
is organised as answers to eleven questions about the agent, each citing verbatim quotes checked
against the source. The evidence record is the machine-readable form of the same thing.

**Coverage report** — three sheets. `Scenarios` is the answer: every scenario with how
many of the model owner's conversations landed on it, least covered first. `Conversations` is the
working, one row per transcript. `The model owner's grouping` appears only where one was supplied.

**The scenario space on screen.** Every scenario-bearing stage shows the scenario space as cards
rather than a spreadsheet, and from the materiality stage onward **the grid above the list is the
only thing that selects them**: category down, materiality across, the count in each cell, shaded
by how much sits there.

Click as many cells as you like — they union, so two cells show the scenarios in both. A row or
column header takes the whole row or column. Everything picked is listed as chips underneath, each
of which removes itself. Picking nothing shows the whole space. Every cell is an ordinary link, so
it works with scripting off and the back button walks back through your selections.

That replaced three fixed view tabs and five dropdown filters, none of which could express "these
two cells" — which is the thing somebody triaging three hundred scenarios actually wants. The one
control left beside it sets how many rows render: 50 by default, raised to 100, 250 or all, and
the page always states how many matched versus how many are shown.

The grid is also the only view that shows what is *not* in the space — a combination nothing
landed in has no row in a ranked list, and an empty High × Termination cell is usually the
finding.

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
                                                     (writes the metadata; build-data-template writes the template)
assess-materiality  INTAKE REGISTRY_IN REGISTRY_OUT [--no-llm] [--context FILE] [--note TEXT]
review              INTAKE REGISTRY_IN REGISTRY_OUT [--no-llm] [--context FILE] [--note TEXT]
                                                    [--max-proposals N] [--pack FILE]
                                                    [--owner-scenarios FILE]
build-pack          INTAKE METADATA PACK_OUTPUT
generate            INTAKE OUTPUT_PREFIX [--no-llm] [--with-probes] [--context FILE] [--note TEXT]
map-coverage        INTAKE METADATA CONVERSATIONS REPORT [--threshold N]
serve               [--port N] [--workspaces DIR]
```

`ingest` accepts files or directories, and reads a directory one level deep. `generate` runs
build-graph, refine and assess-materiality in one pass.

---

## Configuration

Settings live in two files, split by who owns the answer.

**`.env`** — yours and your machine's: the SafeChain credentials, which model to call, where
SafeChain is. Never committed, and short enough to read at a glance.

**`tuning.yml`** — how the work is run: model tiers, per-stage overrides, batch sizes,
concurrency, how hard ingestion tries, redaction. None of it is secret, all of it is worth a team
agreeing once, and as a table it is legible in a way forty `KEY=value` lines are not. It is
committed, so a change to it is reviewed like any other change. Delete a key and the built-in
default applies; delete the file and the tool runs exactly as it ships.

Every setting in `tuning.yml` also has an environment variable name, and the environment always
wins. That is how you override one setting on one machine for one run without editing a shared
file — and it means an existing `.env` full of `LLM_*` settings keeps working untouched.

**An edit applies to the next call — no restart.** The file is re-read whenever it changes on disk.
On startup the tool prints which file it is reading; if a setting appears to do nothing, check that
line first. A file that will not parse stops the run and names the line, rather than being ignored
and silently reverting every setting to its default.

Calls run on one of three tiers, because the work genuinely differs. Each tier takes its own model,
output cap, reasoning effort and retry count, and each falls back to `LLM_MODEL_ID`, so nothing
changes until you name a smaller model.

| Tier | Used by | Output cap | Reasoning | Retries | Model |
|---|---|---|---|---|---|
| **Judgement** | reading documents, drafting the intake, review, mapping the model owner's conversations | 65,536 | high | 4 | `LLM_JUDGEMENT_MODEL_ID` |
| **Materiality** | weighing each scenario against its peers | 32,000 | medium | 2 | `LLM_MATERIALITY_MODEL_ID` |
| **Standard** | writing scenario text | 16,000 | minimal | 4 | `LLM_MODEL_ID` |

`max_tokens` is the **output** cap, not the context window. Reasoning tokens come out of the same
budget as the reply, so a high reasoning effort against a small cap truncates the JSON rather
than shortening the answer — which is why each tier sets both together.

**If a reply comes back truncated**, reduce the batch size first. Raise the cap only if that does
not resolve it.

### Reading the pack as a loop

`LLM_INTAKE_LOOP` (or `ingestion.intake_loop`) is **off** by default. Switched on, it **replaces**
the reading-and-drafting sequence at the intake stage rather than running after it.

The sequence reads every document against three groups of questions, puts what is left open back
to them twice more, then drafts a declaration, repairs it and reads it back whole: eight to ten
model calls in a fixed order, made whether or not each has anything to do. The loop decides what to open, writes a
declaration, audits it, and goes back only for what is missing — an ordinary pack finishes in
three or four calls, and a large one spends what it needs instead of what the sequence budgeted.

Whether it is finished is never the model's call. That is decided against the declaration on disk
by the same check the interface shows as open questions, so a loop cannot talk itself into
stopping early or run forever because it is not satisfied. Where the documents genuinely do not
settle something, it records a question — and a question that does not name a row the audit is
already raising is refused, because "is DEC-07 clear?" is not a question anybody can answer.

What does not change: files are parsed and redacted deterministically before any tool can read
them, diagrams go through the same audited vision pass, and the declaration is validated against
the intake's own vocabulary and consolidated before it is written. It writes the same context
document and evidence record the sequence did, so no later stage can tell which path produced the
run.

**Capability spans are the one thing it may not write.** Where a block of the agent begins and
ends is drawn by a person against the graph and decides how the whole scenario space is
enumerated; the loop fills in everything else about a capability — its name, its type, what it
does — from the decisions inside the span and the documents describing them, and carries the span
across untouched.

It needs a gateway that supports tool-calling, which not all do. Run `python
tools/probe_agent_support.py` to find out before switching it on; check 10 exercises exactly the
path this uses. A gateway that cannot run it falls back to the fixed sequence and says so in the
stage result, rather than failing the stage.

**How many calls a stage made** is reported when it finishes — in the stage's own result panel in
the interface, and on the last line of the command's output on the command line. Retries inside a
call are not counted again: the number is how much work the stage asked for, not how many times
the transport had to ask for it.

**Every individual call the tool makes can be pointed at its own model**, one level finer than a
tier — the scenario writer, each of the three ingestion reads, the diagram passes, the two review
sweeps, coverage mapping. `tuning.yml` lists all thirteen with the model line commented out and
ready to fill in, and each also takes its own output cap, temperature, reasoning effort,
retries, batch size and concurrency. See **[HOW_IT_WORKS.md](HOW_IT_WORKS.md#tuning)** for the stage keys,
the batching model and every remaining setting.

### An LLM council

Five call sites can be answered by three models instead of one. Two **workers** answer the same
prompt independently and in parallel, knowing nothing of each other; a **reconciler** then answers
the same prompt itself with both readings in front of it, and its answer is the one used.

It is not a vote and not a merge. A vote over two answers cannot break a tie, and a merge of two
JSON documents is a document neither model wrote and neither would defend. The independence is the
whole point: two models shown each other's work converge, and that agreement is one reading with a
second signature on it.

```yaml
council:
  enabled: on
  workers: [a-model, another-model]     # exactly two, ideally not the same model twice
  reconciler: a-strong-model
  stages:
    intake_draft: on
    reviewer_assess: on
```

Off unless you switch it on and name the models. Ten passes can take one, and the reason is the
same for each: it is one judgement over a whole body of evidence that every later stage takes as
given, with nothing downstream that would catch it being wrong. **Reading the documents comes
first** — the three diagram passes and the two text ones — because the intake is drafted from that
reading and the scenario space from that intake, and nothing ever goes back to the documents to
check. Then intake drafting and repair, the review's assessment and proposal sweeps, and coverage
mapping. Writing scenario text and
assigning materiality are deliberately not on the list; those are made per scenario, hundreds of
times, and a bad one is visible on the page beside its neighbours.

Expect roughly three times the tokens of the pass you turn it on for and about twice its wall
time: the two workers overlap, the reconciler waits for them. On a batched pass it is three
flights rather than three calls per chunk. It degrades rather than fails at every step — one
worker down leaves the reconciler with one reading, both down falls back to a single ordinary
call, and a reconciler that does not answer hands back a worker's reading.

---

## Testing

```bash
python -m unittest discover -s tests
```

Standard library only. Beyond unit coverage, several tests exist to protect properties that would
otherwise fail silently:

- The data template contains no expected outcomes or ground-truth columns.
- The scenario writer is never given a scenario's terminal state, so the expected outcome cannot
  reach the data template through the text it writes.
- A quote that was reworded rather than copied from the source is rejected; one mangled by PDF
  extraction is still matched.
- A quote invented from fragments scattered across the pack is rejected; the match must be local.
- A document that cannot be read is refused with a reason, and one unreadable file does not stop
  the rest of the pack being read.
- A run where most model calls failed is abandoned rather than written.
- Facts stated in three separate sections are assembled into one answer.
- A submitted pack is read in a handful of model calls rather than one per passage.
- A drafted intake produces a working scenario space without being edited, every field the drafter
  extracted survives into the workbook, and what the draft leaves structurally broken is put back
  to the model with its own failures named.
- Submitted conversations are read from a workbook with unfamiliar headings and a cover sheet in
  front of the data, a semicolon CSV, or a document with no table at all.
- A conversation is matched to the scenario it *ends* on, never to a longer scenario that merely
  contains it, and "nothing fits" is reported rather than forced to a nearest match.
- Coverage counts conversations rather than flagging scenarios, and moving the representation
  threshold recounts without another model call.
- Changing a stage marks every later stage out of date, and out-of-date output is kept.
- A stopped run writes nothing, and cannot overwrite the run that replaced it.
- The interface uses no Flask API newer than 1.0.
- Each pass runs on the tier its work needs, so a judgement call cannot be quietly demoted.

---

## Project layout

```
metric/
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
python -c "import metric; print(metric.__file__)"
```

---

## Extending

**Changing a prompt.** Every prompt is a file in `the step's prompts/ directory`, named
`<pass>.<purpose>.md`. Edit the wording and run again — nothing is compiled. Anything in double
braces, like `{{use_case}}`, is filled in at run time; leave those exactly as they are. If one is
renamed or removed, the run stops with a message naming the file and the slot.

**Adding a probe.** Append an entry to `probe_library.yaml` with an id, family, name, intent,
expectation, applicability predicate and turn count. The test suite rejects an entry that names a
domain-specific object or references an unknown predicate.

**Adding an applicability predicate.** Add it to `PREDICATES` in `core/probes.py` as a function of
the intake.

**Adding a document format.** Add a reader to `ingest/readers.py` and one entry to its registry of formats.

**Adjusting variation counts.** `VARIATIONS_BY_MATERIALITY` in `core/models.py`.
