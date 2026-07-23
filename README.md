# Agentic Scenario Generator

Builds a test benchmark for a conversational AI agent from a structured description of that
agent, issues it to the team that owns the agent as a workbook they fill in, and measures how
much of the benchmark that team's own testing already covers.

Written for independent validation: the benchmark is generated from a declared model of the
agent rather than from the owner's test cases, and the expected outcomes never leave the
validating team.

---

## Contents

- [The problem](#the-problem)
- [How it works](#how-it-works)
- [Installation](#installation)
- [Quick start](#quick-start)
- [The intake workbook](#the-intake-workbook)
- [Commands](#commands)
- [Outputs](#outputs)
- [Probes](#probes)
- [Materiality](#materiality)
- [Coverage mapping](#coverage-mapping)
- [Configuration](#configuration)
- [Extending](#extending)
- [Testing](#testing)
- [Project layout](#project-layout)

---

## The problem

Testing a conversational agent is not like testing a model that returns a number. The agent
decides what to do at each step, calls tools, and reaches an outcome through a route that varies
between runs. Two things follow.

First, a test set assembled by hand drifts towards the paths its author already had in mind.
Failure routes, rare branches and outcome combinations go untested, and nobody can say by how
much.

Second, when the team that built the agent also writes the tests, the tests inherit the same
assumptions as the implementation. An independent reviewer needs a benchmark derived from a
declared model of the agent, not from the tests that model already passes.

This tool addresses both. It enumerates the agent's decision graph exhaustively, so coverage is
a property of the graph rather than of anyone's imagination, and it keeps every expected outcome
on the validating side.

---

## How it works

Six stages. The first three are deterministic and involve no language model; the rest add
description, judgement and review on top.

```
init-template  →  build-graph  →  build-probes  →  refine  →  assess-materiality  →  review
                                                                                       │
                                       map-coverage  ←────────────────────────────────┘
```

| Stage | LLM | What it does |
|---|---|---|
| `init-template` | no | Writes a blank intake workbook for the agent's owner to complete. |
| `build-graph` | no | Walks the decision graph exhaustively; every distinct path becomes a scenario with its expected route recorded. |
| `build-probes` | no | Adds adversarial and non-functional probes that apply to this agent. |
| `refine` | yes | Writes each scenario's business description and tester script. |
| `assess-materiality` | yes | Assigns Low / Medium / High / Critical using cross-scenario signals. |
| `review` | yes | Final sweep over the whole benchmark: settles materiality, flags problems, proposes gaps. |
| `build-pack` | no | Writes the challenge pack from a registry. |
| `map-coverage` | yes | Matches the owner's own test scenarios against the benchmark. |

`generate` runs build-graph, refine and assess-materiality in one pass without intermediate
files, for when the staged workflow is not needed.

**Why the split.** Enumeration is deterministic and auditable: given an intake, the same
scenarios come out every time, and any scenario can be traced back to the path that produced it.
Language models are used only where judgement is genuinely required — turning a path into
readable instructions, weighing business consequence, and reviewing the finished set. No model
call decides which scenarios exist.

---

## Installation

Python 3.9 or later.

```bash
git clone <repository-url>
cd scenario_generator_pkg
pip install -r requirements.txt
python -m scenario_generator --help
```

No build or packaging step, and nothing to add to your path — run it from the repository root
and the package is found where it sits.

For the stages that call a language model, copy `.env.example` to `.env` and fill in the
gateway details. Every stage also runs with `--no-llm`, which substitutes deterministic
placeholder text — useful for validating an intake before spending any model calls.

---

## Quick start

```bash
# 1. Produce a blank intake and send it to the team that owns the agent.
python -m scenario_generator init-template intake.xlsx

# 2. With the intake returned, generate the benchmark.
python -m scenario_generator generate intake.xlsx acme --with-probes

# 3. Review the finished benchmark as a whole, rebuilding the pack with the result.
python -m scenario_generator review intake.xlsx acme_registry.xlsx acme_registry.xlsx \
    --pack acme_challenge_pack.xlsx
```

This produces `acme_challenge_pack.xlsx` — issued to the agent's owner — and
`acme_registry.xlsx`, which stays with the validating team.

Supply extracts from the agent's documentation with `--context notes.md` to ground the generated
descriptions in real product and policy detail. It is optional; everything needed is otherwise
derived from the intake.

Once the owner returns their own scenario library:

```bash
python -m scenario_generator map-coverage intake.xlsx acme_registry.xlsx owner_scenarios.xlsx overlap.xlsx
```

---

## The intake workbook

The intake is the single source of truth for the agent's structure. It should correspond
one-to-one with a diagram of the agent: anything absent from the intake is absent from the
benchmark.

| Sheet | Purpose |
|---|---|
| `Guide` | Field-by-field instructions with worked examples. |
| `L1 Use Case` | Name, business objective, agent type, channel, handoff triggers, safety requirements, success criteria. |
| `Personas` | The kinds of user the agent serves, and which outcomes each is associated with. |
| `L2 Capabilities` | What the agent can do, each typed as Lookup, Transactional, Gating, Advisory or PII-handling. |
| `L3 Decisions` | Branch points and their named outcomes. |
| `L4 States` | Positions the interaction can occupy, and which decisions are available from each. |
| `Tools` | Systems the agent calls, and whether each changes stored state. |

Four fields carry more weight than their size suggests:

**`L2 Capabilities.Type`** decides which probes apply. A closed vocabulary rather than free text.

**`L3 Decisions.Input Source`** — `User`, `Tool`, `Memory-Session`, `Memory-CrossSession`,
`System-Context` or `Document`. Only `User` steps become conversational turns. This is what
allows agents that plan internally, or run without a conversation at all, to be described: a
path with no user step is a single trigger rather than a multi-turn script.

**`L3 Decisions.Max Attempts`** bounds retry loops per decision. There is no global limit, so a
decision that genuinely allows three attempts produces three-attempt paths.

**`L4 States.Outcome Type`** on a terminal state sets the category of every scenario ending
there — Happy path, Retry, Fallback, Escalation or Termination. Declaring it is preferable to
letting the wording of an outcome be interpreted.

---

## Commands

```
init-template       OUTPUT
build-graph         INTAKE GRAPH_OUTPUT [--with-probes]
build-probes        INTAKE GRAPH_INPUT GRAPH_OUTPUT
refine              INTAKE GRAPH_INPUT OUTPUT_PREFIX [--no-llm] [--context FILE]
assess-materiality  INTAKE REGISTRY_IN REGISTRY_OUT [--no-llm] [--context FILE]
review              INTAKE REGISTRY_IN REGISTRY_OUT [--no-llm] [--context FILE]
                                                    [--max-proposals N] [--pack FILE]
                                                    [--owner-scenarios FILE]
build-pack          INTAKE REGISTRY PACK_OUTPUT
generate            INTAKE OUTPUT_PREFIX [--no-llm] [--with-probes] [--context FILE]
map-coverage        INTAKE REGISTRY OWNER_SCENARIOS REPORT
```

Passing the same path as input and output updates a registry in place. `build-probes` is
idempotent: running it twice replaces the probes rather than duplicating them.

**The challenge pack is derived from the registry and must be rebuilt whenever the registry
changes.** `refine` and `generate` write one for convenience, but materiality drives the
requested run count and the review pass can change it or add scenarios — so a pack written
before the review is out of date. Either pass `--pack` to `review`, or run `build-pack`
afterwards. `review` warns when it has changed something and no pack path was given.

---

## Outputs

### Challenge pack — issued to the agent's owner

Five sheets. Two are reference, two are filled in, and one explains the exercise.

| Sheet | Role |
|---|---|
| `Instructions` | What to run, what to record, what not to edit. |
| `Scenarios` | One row per scenario: description, persona, starting situation, recommended turns, required runs. |
| `Turn_Plan` | One row per scenario and turn: what the tester should induce. |
| `Run_Log` | One row per scenario, run and turn, pre-filled with identifiers. |
| `Run_Summary` | One row per scenario and run. |

For a probe, the `Turn_Plan` rows are an approach to work through rather than a line-by-line
script: a probe has no decision path to walk, so the tester pursues a line of attack and the
stated turn count is a floor rather than a contract.

`Run_Log` is pre-populated down to the exact number of runs required, so the workload is fixed
rather than something the owner has to construct. Read in order it forms the transcript, so
there is no separate transcript field to reconcile. Tool activity and reasoning traces are
free-form: whatever the owner's framework already emits can be pasted in as-is.

**The challenge pack contains no expected outcomes.** No decision path, no expected variant, no
expected tool call, no category, no materiality. A test asserts this on every build.

### Registry — retained by the validating team

`Scenario_Metadata` (full metadata and the expected outcome), `Turn_Metadata` (expected outcome
per turn), and `Scenario_Text` (the description and script as issued).

### Overlap report

`Overlap` (benchmark scenarios and whether the owner covered each), `Owner_Incremental` (owner
scenarios falling outside the declared model), and `Summary`.

---

## Probes

Some failures have nothing to do with which route the agent takes. Whether it can be talked into
revealing its instructions, or will invent a confident answer when it has no basis for one, is a
property of the agent rather than of any path through it. Anchoring such tests to individual
paths tests the same property repeatedly and calls it coverage.

Probes are therefore standalone. `probe_library.yaml` holds 27 of them across seven families —
instruction integrity, scope and authority, tool and action integrity, context and memory,
information disclosure, truthfulness, and conduct and fairness — anchored to the OWASP Top 10
for Agentic Applications where applicable.

Four rules govern the library, and the test suite enforces two of them:

1. **One property per probe.** A probe tests a single invariant, not a single attack. Where the
   same property can be attacked through several delivery vectors — an override delivered
   plainly, encoded, in another language, wrapped in fiction — those belong in one escalating
   probe rather than several near-duplicates. Splitting them inflates the benchmark without
   testing anything new, and tests persistence less well: an agent that deflects the first
   vector and yields to the fourth is only caught when all four occur in one conversation.
2. **Domain-neutral.** A probe states a property any conversational agent must hold, never a
   scenario from a particular industry. Domain detail is supplied when the probe is written up
   for a specific agent, which is why the same library serves every use case unchanged.
3. **Assessable from the transcript.** A probe whose verdict depends on internal state the
   validating team cannot observe does not belong here.
4. **Objective applicability.** Which probes apply is decided by predicates over what the intake
   declares — `has_tools`, `touches_state_change`, `has_authentication`, `handles_pii`,
   `has_persistent_memory`, `ingests_user_content` — never by inference. An unrecognised
   predicate excludes the probe rather than including it everywhere.

Probes receive `NF-xxx` identifiers and carry their expected behaviour in the registry. The
probe's family and expectation never appear in the challenge pack, for the same reason expected
outcomes do not.

Scope is bounded by what the owner can actually do. Threats requiring compromised dependencies,
code execution, inter-agent interception or infrastructure faults cannot be induced by holding a
conversation with the agent, and belong to architectural review rather than to this benchmark.

---

## Materiality

Each scenario carries a tier of Low, Medium, High or Critical, which determines how many times
the owner is asked to run it.

Assignment happens in its own pass rather than alongside the description, because it depends on
comparison. Whether a scenario matters is partly a question of what else is in the benchmark: a
shallow variant of a path already covered thoroughly warrants less attention than the same
scenario would in isolation. Redundancy and relative depth are computed across the whole set
before any model call, and supplied as evidence rather than left to be inferred.

Two passes can assign it, and a human can override either. In precedence order:

| Column | Set by | Wins over |
|---|---|---|
| `Materiality Override` | a human reviewer | everything |
| `Reviewed Materiality` | the review pass, with the whole benchmark visible | the initial assessment |
| `Materiality` | the materiality pass | — |

`Effective Materiality` resolves the three and drives the run count. Earlier values are never
overwritten, so the reasoning behind a tier remains visible.

### The review pass

`review` runs last and is the only stage that sees the benchmark whole. It receives the use
case, the agent's full declared structure, a description of every registry field, and a digest
of every scenario generated. It may:

- **Settle** each scenario's materiality, with the deterministic redundancy evidence and the
  whole benchmark in view, and a rationale giving the business consequence of failure. Running
  `assess-materiality` first is therefore optional — it is useful when you want tiers before
  reviewing, but `review` reaches its own verdict either way.
- **Flag** a scenario as `Redundant`, `Under-specified` or `Mis-scoped`.
- **Propose** additional scenarios, capped and validated against the intake's vocabulary, marked
  `llm-proposed` with `LP-xxx` identifiers.

Passing `--owner-scenarios` additionally shows it what the agent's own team already tests, as
context for judging where attention has and has not gone.

It cannot remove anything: a flag is a recommendation for a human. Proposals are aimed at what
enumeration structurally cannot reach — a user changing intent partway through, abandoning a
journey, contradicting themselves across turns, or a case sitting either side of a declared
threshold.

---

## Coverage mapping

`map-coverage` takes the owner's own scenario library as free text and reports how much of the
benchmark it covers.

Each owner scenario is mapped onto the intake's vocabulary; anything outside that vocabulary is
discarded rather than guessed at. Matching then applies a deliberately strict rule:

**Decision path and persona form a single gate.** Both must match exactly, or the verdict is
no match. A persona mismatch is not treated as a weaker match, because the same route walked by
a different kind of user is a different test.

Only after that gate passes does other metadata come into play, and only to demote a full match
to partial — never to create a match. Every demotion and gate failure is recorded, so a verdict
can always be explained.

Extraction confidence is reported separately as `Confident` or `Watch-out`, because how well a
scenario was understood is a different question from whether it matched.

Coverage is measured against the functional benchmark only. Probes and proposals are excluded by
origin.

---

## Configuration

Copy `.env.example` to `.env`:

```
IDAAS_APP_ID / IDAAS_KEY / IDAAS_URL     Gateway authentication.
LLM_ENDPOINT / LLM_MODEL_ID / LLM_SCOPE  Chat-completions endpoint and model.
LLM_TEMPERATURE                          Default 0.3.
LLM_MAX_TOKENS                           Default 16000.
LLM_REASONING_EFFORT                     Default "minimal".
LLM_JUDGEMENT_MAX_TOKENS                 Default 32000.
LLM_JUDGEMENT_REASONING_EFFORT           Default "high".
```

Any gateway accepting a standard chat-completions request body will work.

There are two budgets because the passes do different work. Writing descriptions and extracting
metadata are mechanical: the answer follows from the input and the reply is bounded by the batch
size. Assessing materiality and reviewing the benchmark are not — those passes weigh each
scenario against the whole set and must justify the verdict, so they need room to reason and to
write.

Reasoning tokens come out of the same budget as the reply. A high reasoning effort against a
small cap truncates the JSON rather than producing a shorter answer, which is why the two
judgement settings move together.

Every model call is batched, and any record a batch omits is retried individually, so a single
malformed reply cannot silently drop scenarios. Replies are parsed leniently — markdown fences,
surrounding prose and unescaped newlines are all tolerated, and individual records are salvaged
from a reply that will not parse as a whole.

If you see truncation warnings, reduce the batch size first — a smaller batch usually resolves
it and keeps replies easier to parse. Raise the token budgets only if that does not.

---

## Extending

**Adding a probe.** Append an entry to `probe_library.yaml` with an id, family, name, intent,
expectation, applicability predicate and turn count. Nothing else needs to change. The test
suite will reject an entry that names domain-specific objects or references an unknown
predicate.

**Adding an applicability predicate.** Add it to `PREDICATES` in `core/probes.py` as a function
of the intake. It becomes available to `applies_when` immediately.

**Changing terminology.** Probe family names live only in the library; scenario categories live
in `core/models.py`.

**Adjusting run counts.** `RUNS_BY_MATERIALITY` in `core/models.py` maps each tier to a number
of runs.

---

## Testing

```bash
python -m unittest discover -s tests
```

64 tests, standard library only. Beyond unit coverage of graph traversal, matching and parsing,
several tests exist to protect properties that would otherwise fail silently:

- The challenge pack contains no expected outcomes, no probe expectations and no ground-truth
  columns.
- No probe names a domain-specific object.
- No probe references an unknown applicability predicate.
- A review revision never overwrites the original assessment, and a human override always wins.
- A failed model call leaves the registry unchanged rather than partially written.
- Exhausting a decision's retry limit is recorded as a path rather than silently dropped.

---

## Project layout

```
scenario_generator/
    core/          Domain logic: intake parsing, decision graph, scenario
                   generation, probes, proposals, coverage matching.
                   No I/O beyond the intake workbook, no model calls.
    llm/           Gateway client, shared prompt context, and the four passes:
                   writer, materiality, reviewer, extractor.
    io/            Workbook reading and writing.
    utils/         Text, JSON and batching helpers. No internal dependencies.
    cli.py         Argument parsing.
    pipeline.py    Stage orchestration.
    probe_library.yaml
tests/             Standard-library unittest suite.
examples/          A worked intake, the business context that accompanies it,
                   and a script that runs the full pipeline.
```

Troubleshooting an unexpected command error: confirm which copy of the package is being
imported.

```bash
python -c "import scenario_generator; print(scenario_generator.__file__)"
```
