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

Python 3.9 or later. There is no build step and nothing to add to your path.

**Windows**

```
git clone <repository-url>
cd scenario_generator_pkg
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py -m scenario_generator --help
```

**macOS and Linux**

```bash
git clone <repository-url>
cd scenario_generator_pkg
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m scenario_generator --help
```

Substitute `py` or `python3` for `python` in every command below, to match your platform.

### Connecting a model

Copy `.env.example` to `.env` and fill in your gateway details. Six stages call a language model;
the rest are deterministic and run without one.

```
IDAAS_APP_ID / IDAAS_KEY / IDAAS_URL     Gateway authentication.
LLM_ENDPOINT / LLM_MODEL_ID / LLM_SCOPE  Chat-completions endpoint and model.
```

To check the connection:

```bash
python -c "from scenario_generator.llm.gateway import ask_llm; print(ask_llm('You are terse.', 'Say OK.'))"
```

Every model-using stage also accepts `--no-llm`, which substitutes placeholder text. Useful for
checking an intake before spending any calls.

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
| 2 | Open questions | The above | What the documents did not settle, as questions you can answer inline |
| 3 | Intake | A drafted or completed intake workbook | The agent as a decision graph, confirmed by you |
| 4 | Benchmark | The intake | Every distinct route through the graph, plus applicable probes |
| 5 | Scenario text | The benchmark | A description and tester script per scenario |
| 6 | Materiality | The benchmark | A Low / Medium / High / Critical tier per scenario, driving run counts |
| 7 | Final review | The whole benchmark | Settled materiality, flagged weaknesses, proposed additions |
| 8 | Their coverage | Their scenario library | How much of the benchmark they already exercise, annotated onto each scenario |
| 9 | Issue | The registry | The challenge pack to send, and the registry to keep |

Stages 1, 2 and 8 are optional. Skip 1 and 2 if you already have an intake; skip 8 if the team
submitted no testing of their own.

Stage 1 reads `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.xlsm`, `.csv`, `.md` and `.txt`, and describes
`.png`, `.jpg` and `.jpeg` diagrams with a vision-capable model. Spreadsheets are read sheet by
sheet with the header repeated on each passage, because a rules table is where the thresholds
usually live. A scanned PDF has no text layer and is reported as unreadable rather than read as
empty — ask for a text-based copy.

Every submitted document is named in the reading prompt and the result says which ones any answer
actually rested on. A file that informed nothing is reported: it is either irrelevant or it was
passed over, and those need different responses.

Each stage states how its output was produced — **computed**, **model judgement**, or **human
decision** — because a materiality tier and a graph walk do not deserve the same trust.

Changing an earlier stage marks the later ones out of date rather than leaving them looking
finished. Their output is kept and stays downloadable.

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

Calls run on one of three tiers, because the work genuinely differs. Each tier takes its own
model, output cap and reasoning effort.

| Tier | Used by | Output cap | Reasoning | Model |
|---|---|---|---|---|
| **Judgement** | reading documents, drafting the intake, materiality, review | 65,536 | high | `LLM_JUDGEMENT_MODEL_ID` |
| **Standard** | writing scenario text | 16,000 | minimal | `LLM_MODEL_ID` |
| **Fast** | mapping their scenarios onto the intake vocabulary | 8,000 | minimal | `LLM_FAST_MODEL_ID` |

Every tier falls back to `LLM_MODEL_ID`, so nothing changes until you name a smaller model.
Pointing the fast tier somewhere cheap is the first saving worth making: that pass is
classification against a closed list, and anything outside the list is discarded by validation
regardless.

```
LLM_TEMPERATURE                 Default 0.3.
LLM_JUDGEMENT_MAX_TOKENS        Default 65536.
LLM_JUDGEMENT_REASONING_EFFORT  Default "high".
LLM_JUDGEMENT_MODEL_ID          Defaults to LLM_MODEL_ID.
LLM_MAX_TOKENS                  Default 16000.
LLM_REASONING_EFFORT            Default "minimal".
LLM_FAST_MAX_TOKENS             Default 8000.
LLM_FAST_REASONING_EFFORT       Default "minimal".
LLM_FAST_MODEL_ID               Defaults to LLM_MODEL_ID.
LLM_MAX_CORPUS_CHARS            Default 2000000 (~500k tokens).
LLM_INGEST_RESOLVE_PASSES       Default 2. How many times an open question is put
                                back to the documents before it is put to a person.
LLM_VISION                      "off" where the gateway rejects images.
LLM_MAX_IMAGE_BYTES             Default 4000000.
```

`max_tokens` is the **output** cap, not the context window. Reasoning tokens come out of the same
budget as the reply, so a high reasoning effort against a small cap truncates the JSON rather
than shortening the answer — which is why each tier sets both together.

`LLM_MAX_CORPUS_CHARS` decides how much submitted text goes into one reading call. A pack larger
than this is split across calls, which reads worse than reading it whole, so raise it before
accepting a split.

`LLM_INGEST_RESOLVE_PASSES` decides how hard the tool tries to answer its own questions. Each pass
is one more call over the whole corpus, and each question it settles is one the modelling team
never has to answer. The last pass also rules on what is left: the points only a person can settle
are asked, and the rest are recorded in the context document without being put to anybody. Set it
to 0 to skip the sweep, which is faster and asks considerably more.

**If you see truncation warnings**, reduce the batch size first. Raise the cap only if that does
not resolve it.

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
    llm/        Gateway client, prompt loader, and the passes.
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
