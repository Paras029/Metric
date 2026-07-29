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
| 1 | Documents | The submitted pack: model documentation, their test scenarios, workflow diagrams, supporting material | A cited context document, and answers to eleven questions about the agent |
| 2 | Open questions | The above | What stops the intake being filled in, as questions you answer inline |
| 3 | Intake | A drafted or completed intake workbook | The agent as a decision graph, confirmed by you |
| 4 | Benchmark | The intake | Every distinct route through the graph, plus applicable probes |
| 5 | Scenario text | The benchmark | A description and tester script per scenario |
| 6 | Materiality | The benchmark | A Low / Medium / High / Critical tier per scenario, driving run counts |
| 7 | Final review | The whole benchmark | Settled materiality, checked categories, flagged weaknesses, proposed additions |
| 8 | Coverage | Their scenario library | How much of the benchmark they already exercise, annotated onto each scenario |
| 9 | Issue | The registry | The challenge pack to send, and the registry to keep |

Stages 1, 2 and 8 are optional. Skip 1 and 2 if you already have an intake; skip 8 if the team
submitted no testing of their own.

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

---

## What you get

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

`Overlap` (each benchmark scenario and whether they covered it), `Owner_Incremental` (their
scenarios falling outside the declared model), `Summary`.

Each covered scenario is also annotated in the registry, under `Their Coverage`. It is an
annotation and nothing more — no scenario is dropped. Whether running something they have already
tested is duplicated effort or independent confirmation depends on how far their testing is
trusted, which is your call rather than the tool's. The annotation never appears in the challenge
pack.

---

## Command reference

```
ingest              SOURCES... OUTPUT_PREFIX
draft-intake        CONTEXT_FILE OUTPUT
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
map-coverage        INTAKE REGISTRY OWNER_SCENARIOS REPORT
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

```
LLM_TEMPERATURE                  Default 0.3.
LLM_MAX_ATTEMPTS                 Default 4. Retries for any tier that does not set its own.
LLM_JUDGEMENT_MAX_TOKENS         Default 65536.
LLM_JUDGEMENT_REASONING_EFFORT   Default "high".
LLM_JUDGEMENT_MODEL_ID           Defaults to LLM_MODEL_ID.
LLM_MATERIALITY_MAX_TOKENS       Default 32000.
LLM_MATERIALITY_REASONING_EFFORT Default "medium".
LLM_MATERIALITY_MODEL_ID         Defaults to LLM_MODEL_ID.
LLM_MATERIALITY_MAX_ATTEMPTS     Default 2.
LLM_MAX_TOKENS                   Default 16000.
LLM_REASONING_EFFORT             Default "minimal".
LLM_FAST_MAX_TOKENS              Default 8000.
LLM_FAST_REASONING_EFFORT        Default "minimal".
LLM_FAST_MODEL_ID                Defaults to LLM_MODEL_ID.
LLM_MAX_CORPUS_CHARS             Default 2000000 (~500k tokens).
LLM_INGEST_RESOLVE_PASSES        Default 2. How many times an open question is put
                                 back to the documents before it is put to a person.
LLM_MAX_OPEN_QUESTIONS           Default 6. How many questions are shown at once.
LLM_MAX_CONCURRENCY              Default 4. How many batched calls run at once.
LLM_VISION                       "off" where the model does not accept images.
LLM_MAX_IMAGE_BYTES              Default 4000000.
PII_REDACTION                    Default "off". Redact submitted documents before any
                                 model call. Requires the internal pii-redactor package.
PII_REDACTION_MODE               Default "masking".
PII_REDACTION_REPLACEMENT_TEXT   Default "[REDACTED]".
PII_REDACTION_SENSITIVITY        strict | balanced | loose. Blank takes the engine default.
PII_REDACTION_EXCLUDE_ENTITIES   Comma-separated detector labels to switch off.
PII_REDACTION_ALLOW              Comma-separated terms to never mask.
PII_REDACTION_THRESHOLDS         Comma-separated name=score pairs.
```

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

Writing scenario text, weighing materiality, reviewing the benchmark and mapping owner scenarios
each split a large benchmark into chunks. Every chunk's call now goes out together rather than one
after another — nothing in one chunk's answer depends on another's, so there is no reason the
second should wait for the first to come back. `LLM_MAX_CONCURRENCY` caps how many are in flight
at once; raise it if your gateway comfortably takes more, lower it if calls start failing under
load.

The chunk size is different at each stage, and each was picked for what that particular call is
actually weighing, not from one shared default:

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
to how much one call can weigh carefully at once, which is a much smaller number. A batch size is
a constructor argument on each pass's class (`ScenarioWriter(batch_size=...)` and so on) if a
particular benchmark's shape calls for a different balance.

Document ingestion reads its three question groups in parallel, each image in a submitted diagram
pack is read on its own and in parallel with the others, and parsing several submitted files
(PDF, Word, Excel...) into text also happens in parallel — none of that is affected by
`LLM_MAX_CONCURRENCY`, since none of it is chunked the way the stages above are: there are only
ever a few facet groups or a few files in flight at once regardless of benchmark size.

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
- A scenario library is read from a workbook with unfamiliar headings, a semicolon CSV, or a
  numbered list with no table at all.
- Changing a stage marks every later stage out of date, and out-of-date output is kept.
- The interface uses no Flask API newer than 1.0.
- Each pass runs on the tier its work needs, so a judgement call cannot be quietly demoted.

---

## Project layout

```
scenario_generator/
    core/       Intake parsing, decision graph, scenario generation, probes,
                proposals, coverage matching, evidence, grounding.
    ingest/     Document readers, extraction, context assembly, intake
                drafting, owner scenario library parsing.
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
