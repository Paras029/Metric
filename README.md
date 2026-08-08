# Agentic Scenario Generator

Builds an independent test benchmark for a conversational AI agent.

You give it the documentation a model owner submitted about their agent. It reads that
documentation, drafts a structured description of the agent, enumerates every distinct route
through it, adds a library of adversarial probes, and produces two workbooks: a **challenge pack**
you issue to the model owner, and a **registry** you keep.

The challenge pack contains no expected outcomes. That separation is the point of the exercise.

**[HOW_IT_WORKS.md](HOW_IT_WORKS.md)** is the companion document: what each stage does and why,
how documents and diagrams are read, how coverage is measured, and every setting in the tuning
file. This page is setup, launch and the shape of the thing.

---

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
# Read what the model owner sent.
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

Work started in the interface is stored under `workspaces/`, one directory per use case, holding
the same files these commands produce. A use case can move between the two freely.

---

## The stages

| # | Stage | Input | Output |
|---|---|---|---|
| 1 | Documents | The submitted pack: model documentation, the transcripts of the model owner's own testing, workflow diagrams, supporting material | A cited context document, and answers to eleven questions about the agent |
| 2 | Intake | A drafted or completed intake workbook | The agent as a decision graph, confirmed by you |
| 3 | Benchmark | The intake | Every distinct route through the graph, plus applicable probes |
| 4 | Scenario text | The benchmark | A description and tester script per scenario |
| 5 | Materiality | The benchmark | A Low / Medium / High / Critical tier per scenario, driving run counts |
| 6 | Final review | The whole benchmark | Settled materiality, checked categories, flagged weaknesses, proposed additions |
| 7 | Coverage | The transcripts of the model owner's own testing | How many of those conversations land on each scenario, and which scenarios none of them reach |
| 8 | Issue | The registry | The challenge pack to send, and the registry to keep |

Stages 1 and 7 are optional. Skip 1 if you already have an intake; skip 7 if the model owner
submitted no testing of their own.

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

Any stage accepts free-text notes and extra files. Both are passed to every stage that follows.
On the command line this is `--note`, which is repeatable:

```bash
python -m scenario_generator review intake.xlsx registry.xlsx registry.xlsx \
    --note "Disputes over 500 always go to a person." \
    --note "The vendor document is a version behind."
```

**[HOW_IT_WORKS.md](HOW_IT_WORKS.md#the-stages-in-detail)** covers each stage properly: how the
documents are read, how a workflow diagram becomes a decision graph, what the intake workbook's
load-bearing fields do, and how coverage is measured.

---

## What you get

**Challenge pack** — issued to the model owner. Five sheets: `Instructions`, `Scenarios`,
`Turn_Plan`, `Run_Log`, `Run_Summary`. `Run_Log` is pre-populated to the exact number of runs
required, so the workload is a fixed request rather than something the model owner has to
construct; read in order it forms the transcript. **It contains no expected outcomes** — no
decision path, no expected tool call, no category, no materiality. A test asserts this on every
build.

**Registry** — kept by you. `Scenario_Metadata` (full metadata and expected outcome),
`Turn_Metadata` (expected outcome per turn), `Scenario_Text` (the description and script as
issued).

**Context document and evidence record** — written by the documents stage. The context document
is organised as answers to eleven questions about the agent, each citing verbatim quotes checked
against the source. The evidence record is the machine-readable form of the same thing.

**Coverage report** — three sheets. `Scenarios` is the answer: every benchmark scenario with how
many of the model owner's conversations landed on it, least covered first. `Conversations` is the
working, one row per transcript. `The model owner's grouping` appears only where one was supplied.

**The benchmark on screen.** Every scenario-bearing stage shows the benchmark as cards rather than
a spreadsheet. Three view tabs narrow to a starting point (**Needs attention**, **Critical and
high**, **All**), and dropdown filters narrow further within whichever tab is active. 50 rows
render at a time; the **Show** control raises that to 100, 250 or all of them, and the page always
states how many matched versus how many are rendered.

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
                                                     (writes the registry; build-pack writes the pack)
assess-materiality  INTAKE REGISTRY_IN REGISTRY_OUT [--no-llm] [--context FILE] [--note TEXT]
review              INTAKE REGISTRY_IN REGISTRY_OUT [--no-llm] [--context FILE] [--note TEXT]
                                                    [--max-proposals N] [--pack FILE]
                                                    [--owner-scenarios FILE]
build-pack          INTAKE REGISTRY PACK_OUTPUT
generate            INTAKE OUTPUT_PREFIX [--no-llm] [--with-probes] [--context FILE] [--note TEXT]
map-coverage        INTAKE REGISTRY CONVERSATIONS REPORT [--threshold N]
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

**How many calls a stage made** is reported when it finishes — in the stage's own result panel in
the interface, and on the last line of the command's output on the command line. Retries inside a
call are not counted again: the number is how much work the stage asked for, not how many times
the transport had to ask for it.

**Every individual call the tool makes can be pointed at its own model**, one level finer than a
tier — the scenario writer, each of the three ingestion reads, the diagram passes, the two review
sweeps, coverage mapping. `tuning.yml` lists all thirteen with the model line commented out and
ready to fill in, and each also takes its own output cap, temperature, reasoning effort, retries,
batch size and concurrency. See **[HOW_IT_WORKS.md](HOW_IT_WORKS.md#tuning)** for the stage keys,
the batching model and every remaining setting.

---

## Testing

```bash
python -m unittest discover -s tests
```

Standard library only. Beyond unit coverage, several tests exist to protect properties that would
otherwise fail silently:

- The challenge pack contains no expected outcomes or ground-truth columns.
- The scenario writer is never given a scenario's terminal state, so the expected outcome cannot
  reach the pack through the text it writes.
- A quote that was reworded rather than copied from the source is rejected; one mangled by PDF
  extraction is still matched.
- A quote invented from fragments scattered across the pack is rejected; the match must be local.
- A document that cannot be read is refused with a reason, and one unreadable file does not stop
  the rest of the pack being read.
- A run where most model calls failed is abandoned rather than written.
- Facts stated in three separate sections are assembled into one answer.
- A submitted pack is read in a handful of model calls rather than one per passage.
- A drafted intake produces a working benchmark without being edited.
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
