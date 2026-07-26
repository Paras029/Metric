# Handover — Agentic Scenario Generator v5

**Status:** 73/73 tests passing. All eight commands verified end to end with `--no-llm`.
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
init-template → build-graph → build-probes → refine → assess-materiality → review
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
| `init-template` | no | blank intake workbook |
| `build-graph` | no | exhaustive graph walk → scenarios + per-turn expected outcomes |
| `build-probes` | no | appends applicable probes; idempotent |
| `refine` | yes | description + turn plan (separate prompt for probes) |
| `assess-materiality` | yes | tier assignment with cross-scenario signals |
| `review` | yes | whole-benchmark sweep: settle materiality, flag, propose |
| `build-pack` | no | challenge pack from a registry |
| `map-coverage` | yes | owner scenarios → benchmark matching |

`generate` = build-graph + refine + assess-materiality in memory. Flags: `--no-llm`,
`--with-probes`, `--context FILE`, `--max-proposals N`.

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

The scale lives once in `llm/prompts.py` and is shared by both passes, as are the peer signals
(now in `core/generation.py`, deterministic domain logic rather than LLM-layer code).

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

## 10. Next session

Suggested order:

1. **Stage 0 — document ingestion.** Agreed as the next build. Takes the model document, vendor
   documents, workflow diagrams and slide decks submitted by the model owner, and produces three
   artifacts: an evidence record (every extracted claim with its source document and page, the
   machine-readable state), a cited context document for the `--context` flag, and a gap report
   phrased as questions for the human. A second command drafts a pre-filled intake workbook from
   the evidence record, schema-identical to `init-template` output so `build-graph` consumes it
   whether or not the human edits it. Grounding is the hard requirement: every claim carries a
   verbatim quote, a deterministic pass verifies that quote against the source text, and anything
   unverifiable is discarded rather than trusted. Diagram-derived claims cannot be verified this
   way and are always marked for human confirmation.
2. Fill `.env` and run the full pipeline against a real intake with a real model — the rewritten
   prompts have not been seen against a live gateway.
3. Spot-check probe descriptions and review output for quality.
4. Decide whether review proposals need a human approval gate before issue.
5. Intake slimming and validation layer. Note a latent defect to fold in: `read_intake` does
   `personas[0] = ...` without checking the list is non-empty, so an intake with no personas
   fails with a bare `IndexError`.
6. Graph extraction from model documentation (the independence gap in §7).

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
