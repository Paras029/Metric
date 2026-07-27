# Handover — Agentic Scenario Generator v5

**Status:** 168/168 tests passing. All eleven commands verified end to end with `--no-llm`.
Document ingestion (stage 0) and the local web interface are built; see §10 for what
remains.
**Supersedes:** all earlier handover documents. The code is the source of truth; this is
written from it.

---

## 1. What this is

Generates a test benchmark for a conversational AI agent from a structured intake describing
that agent as a decision graph, issues it to the agent's owner as a fill-in workbook, and maps
the owner's own test scenarios back against the benchmark to report coverage.

Two artifacts, and the separation between them is the whole point:

| Artifact | Audience | Contains |
|---|---|---|
| **Challenge Pack** | the agent's owner | scenarios to run; **zero** expected outcomes |
| **Registry** | MRMG only | full metadata and per-turn expected outcomes |

A test asserts on every build that no expected outcome, probe expectation or ground-truth column
appears in the pack.

---

## 2. Pipeline

```
ingest → init-template → build-graph → build-probes → refine → assess-materiality → review
                                                                                      │
                                                                     build-pack ←─────┤
                                                                   map-coverage ←─────┘
```

**The challenge pack is derived, not authored.** It must be rebuilt after anything that changes
the registry, because materiality drives run counts and the review pass can revise tiers or add
scenarios. `review --pack PATH` rebuilds it inline; otherwise `review` logs a warning naming the
`build-pack` command to run. This was a real defect: the pack was previously written only at
`refine`, so every review verdict was invisible in the issued workbook.

| Stage | LLM | Function |
|---|---|---|
| `ingest` | yes | submitted documents → verified evidence, context file, open questions |
| `init-template` | no | blank intake workbook |
| `build-graph` | no | exhaustive graph walk → scenarios + per-turn expected outcomes |
| `build-probes` | no | appends applicable probes; idempotent |
| `refine` | yes | description + turn plan (separate prompt for probes) |
| `assess-materiality` | yes | tier assignment with cross-scenario signals |
| `review` | yes | whole-benchmark sweep: settle materiality, flag, propose |
| `build-pack` | no | challenge pack from a registry |
| `map-coverage` | yes | owner scenarios → benchmark matching |
| `serve` | no | the local web interface |

`generate` = build-graph + refine + assess-materiality in memory. Flags: `--no-llm`,
`--with-probes`, `--context FILE`, `--note TEXT` (repeatable), `--max-proposals N`.

**Determinism boundary.** Stages 1–3 are fully deterministic: same intake, same scenarios, every
time, each traceable to the path that produced it. No model call decides which scenarios exist —
only how they are described, weighted and reviewed.

---

## 3. Core design decisions

### 3.1 Scenario identity

    scenario = (decision path, persona, perturbation set)

Functional scenarios have an empty perturbation set. Probes are the opposite degenerate case: no
path, an adversarial persona, a behavioural assertion instead of a terminal state.

Two consequences that are easy to get wrong later:

- **The registry's decision path is an expectation, not a prediction.** A real agent may reach
  the same outcome by another route. Judging that belongs to the evaluation harness. The
  generator's job is to state what a correct route looks like, not to assume the agent takes it.
- **Coverage filters on `origin`, never on "has an empty path."** Probes and proposals can
  legitimately share a path shape with a graph scenario. Origins: `graph`, `coverage-gap`,
  `probe`, `llm-proposed`; only the first two count toward coverage.

### 3.2 Intake fields that carry weight

| Field | Effect |
|---|---|
| `L2 Capabilities.Type` | closed vocabulary (Lookup / Transactional / Gating / Advisory / PII-handling); drives probe applicability |
| `L3 Decisions.Input Source` | only `User` steps become conversational turns — this is what makes planner and non-conversational agents representable |
| `L3 Decisions.Max Attempts` | per-decision retry bound; no global cap |
| `L3 Decisions.Outcome Condition` | records thresholds; not used in traversal, feeds boundary-case proposals |
| `L4 States.Outcome Type` | sets scenario category deterministically; keyword heuristic is fallback only |

### 3.3 Materiality

Reframed around business consequence rather than abstract severity:

- **Critical** — failure means the agent is not fit for the purpose it exists for. Regulatory
  breach, direct financial loss, irreversible wrong action. Deploying as built is not defensible.
- **High** — failure means the agent does not deliver its business objective for a real and
  meaningful set of interactions. A core journey breaks; the user cannot achieve what the agent
  exists to do.
- **Medium** — a real gap, but the objective is still met. Fixing improves experience or removes
  friction. Worth testing, not fundamental.
- **Low** — minor or cosmetic, or a close variant of something covered more thoroughly.

Precedence: `Materiality Override` (human) > `Reviewed Materiality` (review pass) >
`Materiality` (first sweep), resolved into `Effective Materiality`, which drives run counts.
Earlier values are never overwritten — the reasoning stays visible.

The scale lives once in `prompts/shared.materiality_scale.md` and is shared by both passes, as
are the peer signals (in `core/generation.py`, deterministic domain logic rather than LLM-layer
code).

### 3.35 Coverage-gap scenarios

`build-graph` runs two passes. The DFS walks every route through the graph. Then
`augment_variants` checks every declared (decision, outcome) pair against what the walk actually
exercised, and for any pair the walk never reached it emits a focused path — shortest prefix to
that decision, plus the missed outcome — with `origin="coverage-gap"`.

It is a safety net for declared-but-unreachable outcomes: a state whose `Reached Via` does not
resolve, an outcome only offered from a branch cut by `MAX_DEPTH`, or a decision no state lists
in `Valid Next Decisions`. On a well-formed intake it produces nothing, and a non-zero count is
itself a signal that the declared graph is inconsistent.

### 3.4 Probes

27 entries in `probe_library.yaml`, seven families, use-case-agnostic and MRMG-owned. Anchored
to OWASP Top 10 for Agentic Applications (2026) where applicable.

Reduced from 42 by merging delivery vectors of the same property into single escalating probes.
Eight merges: instruction extraction (direct + indirect); instruction override (plain + encoded +
cross-language + delimiter-spoof + fiction-framing + role-hijack); unverified authority
(impersonation + relayed approval + asserted prior consent); internal disclosure (config + source
systems + capability reconnaissance); third-party disclosure (direct + aggregate inference);
refusal persistence (erosion + context dilution); fabrication under pressure (numbers + citations
+ rationale); privilege creep (+ authorisation inheritance). Net effect on the example intake:
39 applicable probes → 24, total scripted turns 148 → 114. Merging is also better testing — an
agent that deflects the first vector and yields to the fourth is only caught when all four occur
in one conversation.

Four rules, two enforced by tests:

1. **One property per probe** — vectors of the same invariant escalate within one probe rather
   than splitting into near-duplicates.
2. **Domain-neutral** — no entry names a domain object. A banned-vocabulary test fails the build.
3. **Transcript-assessable** — no probe whose verdict needs backend state MRMG cannot observe.
4. **Objective applicability** — predicates over declared intake facts only; unknown predicate
   excludes rather than includes.

**Scope boundary:** OWASP ASI04 (supply chain), ASI05 (code execution), ASI07 (inter-agent),
ASI08 (cascading), ASI10 (rogue agents) are excluded because they cannot be induced by holding a
conversation. This is the same criterion that rules out tool-timeout fault injection. They
belong to architectural review.

### 3.5 Review pass

Runs last; the only stage seeing the benchmark whole. Its prompt carries, in order: the mission
(what independent validation is for, and that this is the data-collection phase whose gaps become
validation gaps); what an agentic system is and what this one is, with every declared L1 field
and a derived shape line; the full declared structure; how the benchmark was built, including an
explicit statement of that approach's weakness (bounded by what was declared) so the model knows
where its judgement is actually needed; a field-by-field guide; the deterministic peer signals
explained as evidence; the materiality scale; optionally the owner's own scenarios; and a digest
of every scenario. ~20k characters. Raised reasoning effort (`high`), batch size 6.

Powers: **settle** materiality (own columns, whole set + redundancy evidence in view), **flag**
(`Redundant` / `Under-specified` / `Mis-scoped`), **propose** additions (capped 15, validated
against intake vocabulary, `LP-xxx`, `origin=llm-proposed`). It cannot delete — a flag is a
recommendation.

`assess-materiality` is now optional when `review` runs: review reaches its own verdict with
strictly more context and the same deterministic peer signals. Keeping both is useful only when
tiers are wanted before reviewing.

Proposals target what enumeration structurally cannot reach: intent switching mid-journey,
abandonment, multi-intent turns, self-contradiction across turns, boundary cases either side of
a declared threshold.

### 3.6 Coverage matching

Path **and** persona form a single hard gate — both exact or no match. A persona mismatch is not
a softer tier, because the same route walked by a different user is a different test. Only after
the gate passes can other metadata demote Match → Partial match; it can never create a match.
Every demotion and gate failure is recorded. Extraction confidence is a separate binary column.

---

## 4. Output workbooks

**Challenge pack** (5 sheets): `Instructions`, `Scenarios`, `Turn_Plan`, `Run_Log`,
`Run_Summary`.

`Run_Log` columns: SC ID, Run, Turn, User Input (Actual), Agent Response, Tool Metadata, Agent
Reasoning / Trace, Additional Metadata. Tool Metadata is free-form by design — whatever the
owner's framework emits. Pre-populated to the exact run count so the workload is a fixed
contract. Read in order it *is* the transcript; there is no separate transcript field.

**Registry**: `Scenario_Metadata` (23 columns), `Turn_Metadata`, `Scenario_Text`.

**Overlap report**: `Overlap`, `Owner_Incremental`, `Summary`.

---

## 5. LLM layer

Single model via authenticated gateway, standard chat-completions body. Four passes, all
batched with single-record retry and a salvaging JSON parser.

**Two token budgets**, because reasoning tokens are drawn from the same budget as the reply — a
high reasoning effort against a small cap truncates JSON rather than shortening it:

| | max tokens | reasoning |
|---|---|---|
| Routine (writer, extractor) | `LLM_MAX_TOKENS`, 16000 | `LLM_REASONING_EFFORT`, minimal |
| Judgement (materiality, review) | `LLM_JUDGEMENT_MAX_TOKENS`, 32000 | `LLM_JUDGEMENT_REASONING_EFFORT`, high |

### 5.1 The prompt library

Prompt wording lives in `scenario_generator/prompts`, one plain file per prompt, and is reached
only through `llm/prompt_loader.py`. Nothing else reads those files. The split is deliberate:

- **`prompts/*.md`** — instruction text. Editable by someone who is not changing code, which is
  the point. This is where wording, emphasis and worked examples live.
- **`llm/context.py`** — text *derived from intake data*: the use case description, the declared
  graph, the benchmark digest. This is logic and stays in Python; it changes when the data model
  changes, not when someone wants a prompt to read differently.

Placeholders are `{{doubled_braces}}`, **not** `str.format`. Prompts embed JSON examples, and
under `str.format` every literal brace in those examples would need doubling — which makes the
files hostile to the people the split exists to serve. Only `{{word}}` is substituted; every
other brace passes through untouched.

Rendering is strict in both directions: a slot with no value, and a value with no slot, both
raise `PromptError` naming the file. A rename on either side fails immediately instead of sending
a malformed prompt. `tests/test_prompt_library.py` holds the contract of which prompts exist and
which slots each one takes, so the same mistake is caught before a run.

Four prompts are shared rather than owned by one pass: `shared.mission`,
`shared.materiality_scale` (both judgement passes use the same tier definitions),
`shared.house_style`, and `reviewer.owner_block`.

A supplementary context file supplements all of this and is never a prerequisite — each pass is
fully oriented from the intake alone.

| Pass | Sets |
|---|---|
| `ScenarioWriter` | description, turn plan (separate probe prompt) |
| `MaterialityAssessor` | tier, confidence, rationale — own sweep, deterministic peer signals |
| `ScenarioReviewer` | settled tier, flag, proposals — whole benchmark + peer signals + optional owner scenarios |
| `MetadataExtractor` | owner scenario → intake vocabulary |

---

## 6. Layout

```
scenario_generator/
    core/     models, intake, graph, generation, probes, proposals, coverage, context
    llm/      config, gateway, prompt_loader, context, writer, materiality,
              reviewer, extractor
    prompts/  the prompt library — one file per prompt, editable without code changes
    io/       sheets, workbooks
    utils/    text, json_parsing, batching
    cli.py, pipeline.py, probe_library.yaml
tests/        73 tests, stdlib unittest
examples/     build_claims_intake.py, claims_context.md, llm_demo.py
```

Install: `pip install -r requirements.txt` (openpyxl, requests, python-dotenv, pyyaml). No
packaging step, no path setup. From the repository root:

```bash
python -m scenario_generator <command> ...
python -m unittest discover -s tests
```

The package sits at the repository root rather than under `src/`, which is what removes the
`PYTHONPATH` requirement — with no install step there would otherwise be nothing to put a
`src/` layout on the path. The wrapper scripts that used to do this have been deleted.

Stale-copy diagnostic:
`python -c "import scenario_generator; print(scenario_generator.__file__)"`

---

## 7. Open items

1. **`.env` values are placeholders.** No real model run works until the gateway details are
   filled in. This is the single blocker.
2. **Git repo not confirmed pushed.**
3. **Probe wording never validated against a real intake.** Library loads and binds correctly,
   but LLM-written probe descriptions have not been reviewed on real data.
4. **Probe scripts are approach-shaped, not turn-shaped.** A probe has no decision path, so its
   `Turn_Plan` rows give the line of attack and how to escalate; the turn count is a floor, not
   a contract. Probe text is now written into the graph file at `build-probes` — previously it
   was regenerated as placeholder wording on the next read, which is why probe descriptions and
   scripts looked useless.
5. **Review pass never run against a real model.** Fully tested with stubs; prompt quality on a
   real benchmark is unverified. Its prompt is ~20k characters and it runs at high reasoning
   effort, so watch the first run for truncation warnings. Lower the batch size before raising
   `LLM_JUDGEMENT_MAX_TOKENS`.
6. **Review proposals reach the pack unreviewed.** They flow through like any scenario; `Origin`
   makes them filterable. Gate them if approval should precede issue.
7. **`MAX_PATHS=1000` / `MAX_DEPTH=12`** truncate with a warning rather than silently. Split the
   use case if exceeded.
8. **Reproducibility.** Stages 1–3 deterministic; 4–6 are not. Treat registries as pinned
   artifacts, not regenerable on demand.
9. **No signature stability across intake versions.** Renaming a decision changes every
   signature; no v1↔v2 benchmark comparison.
10. **The graph is owner-declared.** Under-declaration silently shrinks the benchmark and there
   is no detector. Planned mitigation — an extraction layer building a candidate graph from
   documentation and diffing it — is not built.
11. **Multi-agent explicitly out of scope.**
12. **No intake validation layer.** A malformed intake fails late or silently rather than being
    caught on read.

---

## 8. Known weaknesses in the design

Recorded honestly so the next session does not rediscover them.

**Probe/functional ratio inverts on small intakes.** 42 probes are fixed by the library; the
functional count scales with the graph. A 5-scenario example gets 39 probes (89% of the pack).
Self-corrects as the graph grows, but sanity-check the ratio before issuing.

**`handles_pii` and `has_persistent_memory` depend on optional declarations.** If the owner
leaves `Type` blank, four probes silently drop. Objective given the intake, fragile given human
form-filling. An intake validation layer would catch it.

**Category feeds materiality peer-grouping.** Category is now deterministic, so this is much
safer than when an LLM set it — but grouping is exact-match on (category, capability set), which
is a conservative redundancy signal that under-groups near-duplicates.

**Run counts multiply fast.** Runs are per-scenario and materiality-driven. 44 scenarios at
Medium produced 447 `Run_Log` rows. On a real 150-scenario benchmark with several Critical
tiers this becomes a large ask. Run `assess-materiality` before issuing — otherwise everything
sits at the placeholder tier and the count is meaningless.

**The intake is growing.** Eight columns across L3 and L4 now, several optional. Each addition
was justified individually; collectively the form is heavier than when this started. See the
critique discussion for options to slim it.

---

## 9. Defects fixed during the prompt externalisation

Recorded because each one had been shipping silently, and because they explain output quality
problems that were previously put down to model variance.

1. **The graph-scenario writer prompt instructed the model to disclose the answer key.** It asked
   the description to state "what a correct outcome looks like", while the probe prompt in the
   same module forbade exactly that. The writer was also handed `ending_state` — the terminal
   state, i.e. the ground truth. Descriptions ship in the challenge pack, and the pack test only
   asserts that ground-truth *columns* are absent, so outcome text embedded in a description
   passed straight through.

   Fixed on both sides. The prompt now carries a single shared non-disclosure rule
   (`shared.house_style`, used by both writer prompts), and `ScenarioWriter._payload` no longer
   includes the terminal state at all — the guarantee is structural, not an instruction the model
   may or may not follow. Per-step outcomes are still supplied, because the tester has to know
   which condition to induce; the route's destination is not. A test asserts the terminal state
   cannot appear in the writer payload.

2. **A string-escaping bug gave the two writer prompts contradictory formatting instructions.**
   `_WRITE_USER` was a normal (non-raw) triple-quoted string containing `separated by \n`, which
   Python turned into a real newline — so the model was told "separated by ⏎." while the probe
   prompt correctly said `\\n`. This is the most likely source of the inconsistent turn-plan
   formatting. Moving prompts into plain files removes the class of bug entirely: what is written
   in the file is what the model sees.

3. **Materiality guidance was duplicated and mildly self-contradictory.** `MATERIALITY_SCALE` and
   the eight numbered factors in the materiality task prompt restated each other and pulled in
   different directions ("most should sit at Medium or Low" against "do not default everything to
   Medium"). The scale now owns the tier definitions and calibration; the task prompt owns only
   how to read the computed signals attached to each scenario. Calibration is stated as the shape
   to expect rather than a quota, so a genuinely high-risk agent can still come back with several
   Critical tiers.

4. **The reviewer could anchor on a placeholder tier.** It is shown `existing_materiality`, which
   may be a real first-pass assessment or an untouched default where `assess-materiality` was not
   run, with no way to tell them apart. The field guide now says so explicitly and instructs it to
   reach its own tier from the evidence before comparing.

Also strengthened while the prompts were open: worked examples with paired good/bad output in
both writer prompts, an explicit input-side/scoring-side distinction in the house style, explicit
JSON shape examples in every task prompt, and probe turn plans described as a floor with an
escalation instruction rather than a fixed script — which matches the documented design in §7.4
but did not match the prompt before.

## 10. Document ingestion and the interface — what exists so far

### 10.1 Ingestion (stage 0), partially built

Built and tested:

- **`core/evidence.py`** — the evidence record. A claim is a statement, the verbatim quote it
  came from, and a source reference (document, locator, kind). Claims are grouped into eleven
  facets: the intake's six sheets, plus business problem/intended use, policy constraints, scope
  boundaries, known risk areas, the owner's own testing, and domain terminology. The extra facets
  exist because `review` reasons about business consequence and cannot do that from graph
  structure alone.
- **`core/grounding.py`** — the anti-hallucination guard, and the reason the rest is safe to
  build. A claim's quote is checked against the source text it cites; if it is not there the
  claim is **rejected**, not flagged. Matching normalises how text *arrives* (line-wrapped
  hyphenation, typographic quotes, ligatures, whitespace) but not what it *says*, so a reworded
  quote fails. Threshold 0.90 coverage, minimum quote length 25 characters. Claims from diagrams
  or from a human answering a gap question are marked `unverifiable` rather than rejected, and
  always surface for confirmation. Rejections are reported, never dropped silently.

### Ingestion is two passes, and that is the correction that mattered

The first build read one passage at a time and kept a claim only if a verbatim sentence *inside
that passage* stated it. Tested against a real 60-page model document it extracted very little,
and the reason was structural rather than a matter of prompt wording: a fact that no single
passage asserts could not be expressed at all. Almost everything a benchmark needs to know about
an agent is of that kind — a process in one section, its exception three pages later, the
threshold governing it in a table.

The pass is now **survey then synthesise**:

- **Survey** reads each passage and brings back observations, deliberately generously. It is told
  explicitly that it is *not* answering anything, only surrendering raw material, and that a
  peripheral observation costs almost nothing while a missed one cannot be recovered. Partial
  information is wanted: "cases above a threshold go to a person" is worth recording even where
  the passage never says what the threshold is.
- **Synthesis** takes one question and sees *every* observation bearing on it from every document
  at once. This is where scattered material is collected and restructured into an answer. It must
  cite the observation ids it rests on and state its own `unknowns` rather than completing the
  picture from what such a system usually does.

**The grounding trade-off was rebalanced, deliberately.** Observations are still checked, but
against the *whole document* rather than the passage they came from, and `MATCH_THRESHOLD`
dropped from 0.90 to 0.78. The two failures are not equally costly here: a fabricated quote is
caught at almost any threshold because invented text shares little with the document, whereas a
real quote fails when the model tidied punctuation or joined two sentences. Losing those is how
extraction ends up thin. Synthesised answers are explicitly derived and cite their observations,
so a reader traces answer → observation → page and restructuring stays visible as restructuring.

Chunks now overlap by one segment, so a process spanning a page break is seen whole by at least
one passage. `EvidenceRecord` gained `answers: List[FacetAnswer]`, and `empty_facets` is judged
on the synthesised answer — scattered observations never assembled into an answer are not an
answer.

Also built:

- **`ingest/readers.py`** — one reader per format behind `read_document`, each returning
  *segments* with locators (page, slide, heading) rather than one block of text, because the
  locator is what a citation later points at. Third-party parsers are imported inside the reader
  that needs them, so a package missing from the internal mirror disables one format rather than
  the tool. A file that yields no text — a scanned PDF, a deck of exported images — is refused
  with a reason rather than contributing nothing silently.
- **`ingest/extraction.py`** — map then check. Each passage goes to the model on the judgement
  budget; every claim returned is put through the grounding check against that same passage
  before it is kept. One unreadable document, or one failed call, costs that document or that
  passage and not the run.
- **`ingest/context_document.py`** — deterministic assembly of the cited context file, and
  `open_questions`, which reads the record from the other side: categories nothing addressed, and
  statements that could not be checked.
- **Prompts** `ingest.system`, `ingest.survey` and `ingest.synthesise`. The survey prompt's
  load-bearing instruction is to collect generously, with the asymmetry spelled out. The
  synthesis prompt's is that the documents will *not* answer the question in one place and the
  model is expected to assemble — while stating unknowns rather than inventing.

Not built yet: image/diagram reading (waiting on the vision check in §12), chunk-level triage to
drop irrelevant passages before the expensive call, conflict detection between documents, and
`draft-intake`. Conflict detection previously sat in `evidence.py` as a function that raised
`NotImplementedError`; it has been removed rather than left in place, since a placeholder that
cannot be called is indistinguishable from a feature that is broken.

**Design decisions already fixed.** Ingestion produces three artifacts: the evidence record
(machine-readable), a cited context document for `--context`, and a gap report phrased as
questions. The context document is **assembled deterministically from verified claims**, not
free-written — a model is used only to group near-duplicates and order them. A prose rendering
was considered and deferred: rewriting verified claims reintroduces the misphrasing risk the
whole design exists to remove, so if one is ever added it must be a secondary artifact that no
stage consumes. Conflicts between documents are surfaced, never silently resolved.

**`draft-intake` writes a real intake workbook**, schema-identical to `init-template` output.
This works because `read_intake` reads cells positionally and looks sheets up by name, so extra
provenance columns to the right and extra provenance sheets are both invisible to it — verified
by reading the code, not assumed. The human reviews and corrects it in Excel and it feeds
`build-graph` whether or not they edit it.

### 10.15 Defects found on the first real run

Run against a real 60-page Word document, and worth recording because two of them were failures
of honesty rather than of function.

1. **The token was assumed to live an hour.** `get_token` cached with a hardcoded `_TOKEN_TTL`
   and ignored what IDaaS actually returned. When the real token expired sooner, every subsequent
   call returned 401 and the cache never refreshed — a silent mid-run death, survey succeeding and
   then everything after it failing. The lifetime now comes from the gateway's own `expires_in`,
   and a 401 re-mints once and retries, because a token can be revoked or shortened server-side
   whatever it claimed on issue. Connection resets and 5xx are retried with backoff; a 4xx that is
   not 401 is raised immediately, since repeating a request the gateway rejected on its merits
   only wastes time.

2. **A completely failed run reported success.** The log read `Answered 11 of 11 questions` when
   all eleven synthesis calls had failed. `FacetAnswer.is_answered` returned true whenever
   `points` was non-empty, and the failure path fills `points` with the raw observations so the
   material is not lost. It now carries `failed`, and a failed answer is never counted as
   answered. Worse, the run wrote its files anyway: `MAX_FAILURE_RATE` now aborts with
   `IngestionFailed` when over half the calls fail, because a thin context file is
   indistinguishable from a document that genuinely said little, and the mistake resurfaces much
   later as a thin benchmark.

3. **Open questions were listed twice.** A facet with no answer produced both a `gap` entry and
   an `unknown` entry carrying the same sentence, doubling the list. Unknowns are now only raised
   for facets that *were* answered — those are the specific points a good answer could not settle.

### 10.17 Flask version compatibility

The interface would not start on the target machine: `@app.post` and `@app.get` are Flask 2.0
shortcuts and the installed Flask was older. Routes now use `app.route(..., methods=[...])` and
files are sent by string path rather than `Path`, both of which work from Flask 1.0 onwards.
Flask stays unpinned in `requirements.txt` so whatever the internal mirror carries will run it.

Worth understanding rather than just fixing: **every test passed while the interface was
unusable.** The development environment had Flask 3.1, where the shortcut exists, so nothing
locally could have caught it. `tests/test_flask_compatibility.py` closes that by asserting
against the source directly — no `@app.post`/`@app.get`, no `send_file(path,` — alongside a check
that the app builds and every expected route is registered with the expected method. The same
reasoning applies to any other convenience added in a newer version of a dependency: if the
target environment cannot choose its versions, the tests have to encode the older interface
rather than trust the local one.

### 10.19 Changes from the second round of real use

**Submitted material is grouped by kind** (`ingest/groups.py`): model documentation, their own
test scenarios, workflow diagrams, supporting material. Each is read the right way and sent to the
stage that needs it. The owner's scenarios are deliberately *excluded* from evidence extraction —
they describe the owner's testing rather than the agent, and reading them as evidence would let
their blind spots into the benchmark through the back door.

**Documents are read in parallel** (`MAX_PARALLEL_DOCUMENTS = 4`). Each passage is an independent
call that spends almost all its time waiting, so four documents finish in roughly the time the
longest takes. Kept modest because a burst is what provokes the throttling the client then has to
back off from. Results are collected in submission order so observation ids stay stable.

**Progress showed two denominators.** A nineteen-passage document displayed "passage 7 of 19"
beside "7/30", the second silently including the eleven synthesis calls still to come. The
message now carries the detail and the bar carries the run; the figure beside it is a percentage.

**The intake is drafted rather than left blank** (`ingest/drafting.py`, `intake.draft` prompt).
The prompt is explicitly instructed to commit: fill what the evidence supports, complete partial
fields as far as it allows and flag them, and leave a field empty only where the evidence says
nothing. A draft that declines everything uncertain is a blank form with extra steps. Confidence
per section and specific review points are written into a **Review This** sheet in the workbook.
Values outside the intake's vocabulary — a capability type, an outcome type — are dropped rather
than kept, since an invented one silently changes which probes apply.

**Stage order and gating.** Coverage now runs *before* Issue: what the owner already covers is
what lets the issued pack concentrate on what they have not. Stages carry `optional`, and
`required_before()` excludes them from gating, so a team holding a completed intake opens the
intake stage on a fresh workspace and works forward without pretending to read documents they
were never sent.

**The owner's scenario library is read from whatever shape it arrived in**
(`ingest/owner_library.py`): workbooks with a cover sheet and a title row, unfamiliar headings,
semicolon CSVs, Word tables, numbered lists in a PDF. It finds the sheet that looks like a
scenario list and the column that holds the description, falling back to the widest column. How
it was read is reported at upload time, while there is still time to send a clearer file.
Refusing all but one layout would have put the coverage measurement out of reach of most
submissions, which is the same as not having it.

### 10.2 The interface

`python -m scenario_generator.webapp`, Flask, localhost only. Server-rendered HTML with
hand-written CSS: **no Node, no npm, no build step.** Node is installed on the target machine but
npm *registry* access is a separate approval from PyPI, and a locally-run Python tool that needs
no `npm install` is one less thing to break on someone else's machine.

- **`webapp/stages.py`** — the ten stages, and each one's `mode`: `computed`, `judged` or
  `review`. This distinction is the interface's organising idea. The pipeline genuinely mixes
  deterministic enumeration with model judgement, and a validator needs to know which produced
  what they are reading. Presenting them identically would be the most misleading thing this
  interface could do.
- **`webapp/workspace.py`** — state, persisted as JSON beside the workbooks. The rule it exists
  to enforce: completing a stage marks every *completed* later stage `stale`. Their outputs are
  **kept** — they are a record of what was issued, and deleting them would be its own data loss —
  but nothing presents them as current. A stale stage can still be re-run.
- **`webapp/app.py`** — routes. Holds no pipeline logic; each stage calls the same functions the
  CLI calls, so the two front ends cannot drift and a workspace can move between them.

**Nine stages, all wired**, and both front ends reach the same capability. Submitted documents
and extracted evidence used to be two stages; nothing happened between them, so the second only
ever asked for a click. They are one stage that takes the pack and reads it.

**Long stages run in the background and report progress.** Reading a sixty-page document is
hundreds of calls over several minutes, and running that inside the request left the browser on a
blank tab with no way to tell a slow run from a dead one. The runner reports through the same
callback the CLI uses, progress is written to the workspace on disk so the polling request can
read it, and the page polls a small JSON endpoint. Work continues if the tab is closed.

**Open questions are answered in place.** Each carries a field; an answer is recorded as a note
with its question attached and joins the context every following stage receives, so answering one
does not mean re-running anything.

**Diagrams are read where the gateway supports vision** (`LLM_VISION`, on by default since the
configured model is multimodal). Images are inlined as base64 content parts by
`ask_llm_with_images`, described by the `ingest.diagram` prompt, and every observation from one is
marked unverifiable — there is no text to check it against — so it surfaces for confirmation.
Where vision is unavailable the diagram is recorded as unreadable with a request for a written
description, and the rest of the pack still reads. Every runner reads
what the previous stage left on disk, calls the same `pipeline.py` function the CLI calls, and
writes its output back, so the registry is the hand-off between them and either front end can
pick up where the other stopped. `pipeline.py` owns the orchestration; `webapp/app.py` owns
routing and presentation and contains no pipeline logic of its own.

The command line gained `ingest` and `serve` to close the gap: everything the interface does is
reachable from the CLI. What the interface adds is the drawn graph, the record of what has run,
and out-of-date marking — presentation of the same work, not extra capability.

- **`webapp/graphview.py`** — the declared graph as an SVG, generated in Python rather than by a
  drawing library so it renders offline with no script and no font download, and so hover detail
  can be a native SVG `<title>` that works without JavaScript. Layered top-down by distance from
  the start state: what a reader wants from the picture is how far a route runs and where it
  ends, and depth read vertically answers both. Terminal states are coloured by outcome type.
  A state nothing leads to is drawn dashed and named in a warning, since that is a defect in the
  declaration rather than a quirk of the drawing.

**Notes.** Every stage takes free text — anything the documents do not say that a later stage
should know. Notes accumulate rather than replace, each carries the stage it was added at, and
every stage that consults context receives all of them: a correction made while reading the
evidence is just as relevant to the final review, and asking for it twice would be a good way to
lose it. The CLI takes the same thing through a repeatable `--note`, assembled alongside
`--context` in `core/context.py`.

Fonts and colour: institutional palette built on Amex deep blue `#00175A` and bright blue
`#006FCF`. Type is a system stack — `--font-sans` in `webapp/static/app.css` is a single
variable, so dropping in a licensed brand typeface is a one-line change plus the font files.

## 11. Next session

Suggested order:

1. **Finish stage 0.** The document readers behind one interface (pypdf, python-docx,
   python-pptx, Pillow — all confirmed reachable from the internal index), chunk-and-triage
   orchestration, the extraction pass and its prompts, deterministic context-document assembly,
   the gap report, and `draft-intake`.
2. **Vision check.** Whether diagrams can be read by the gateway is still unconfirmed. The check
   is in §12. Design so a negative answer degrades to a human-supplied description rather than
   blocking the pipeline.
3. **Wire the remaining stages into the interface** — evidence, questions, scenario text,
   materiality, review, coverage. Long-running stages need progress reporting through a callback
   rather than printing, since the interface has to show something during a multi-minute run.
4. Fill `.env` and run the full pipeline against a real intake with a real model — the rewritten
   prompts have not been seen against a live gateway.
5. Spot-check probe descriptions and review output for quality.
6. Decide whether review proposals need a human approval gate before issue.
7. Intake slimming and validation layer. Note a latent defect to fold in: `read_intake` does
   `personas[0] = ...` without checking the list is non-empty, so an intake with no personas
   fails with a bare `IndexError`.
8. Graph extraction from model documentation (the independence gap in §7).

### Constraints that shape the next build

- **UI is the step after stage 0.** The CLI must keep working, but the stages will be driven from
  a UI where the user corrects intermediate output and resumes. Consequences worth holding to:
  presentation stays in `cli.py`, core and LLM layers return structured results rather than
  printing, progress is reported through a callback, and every stage reads and writes explicit
  artifacts so a UI can show and edit what sits between them.
- **Enterprise packaging.** Dependencies come from an internal Artifactory mirror, not PyPI
  directly. Prefer pure-Python, widely mirrored libraries with small dependency trees. Tesseract
  and other system binaries are not expected to be available. LangChain is not available; an
  internal wrapper exists but is not documented well enough to build on, so document parsing
  should sit behind a small internal interface that such a wrapper could later implement.
- **Vision support is unconfirmed.** Multimodal input is likely available on the gateway but has
  not been verified. Design so diagram extraction degrades to a human-supplied description rather
  than blocking the pipeline.

---

## 12. Checking the gateway for vision support

Still unconfirmed, and it decides how workflow diagrams are handled. Two parts.

**Ask the platform team:**

1. Does the gateway expose a multimodal model, and what is its model ID? A text-only ID rejects
   images regardless of anything else.
2. Does the gateway accept `content` as an array of parts, or only a plain string? This is the
   one that most often blocks it — many enterprise gateways normalise the request body and strip
   or reject the array form even when the model behind them supports vision.
3. What is the maximum request body size? Base64 inflates an image by roughly a third.
4. Is there an approved internal document-intelligence or OCR service? Worth asking before
   building anything; if one exists it is likely better supported than either alternative.

**Run this with a working `.env`:** send a 1×1 pixel PNG as an image part and read the result.
200 with a sensible reply means vision works. A 400 naming `content` means the gateway rejects
the multimodal shape. A 400 naming the model means the model ID is text-only.

Tesseract is assumed unavailable — it needs a system binary, which is a harder approval than a
Python package, so it is not part of any design here.
