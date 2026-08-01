"""Orchestration for every stage of the pipeline. No argparse here — see cli.py for the command
line, and webapp/ for the interface. Both front ends call these functions, which is what keeps
them capable of the same things and stops a workspace from being trapped in one of them.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .core import (DecisionGraph, IntakeData, Scenario, build_probes, enumerate_paths,
                   instantiate_all, load_context, read_intake, read_owner_scenarios)
from .core.representation import DEFAULT_THRESHOLD, build_report
from .io import (read_registry, read_scenarios, write_challenge_pack, write_coverage_report,
                 write_registry, write_scenario_graph)
from .ingest import (DocumentExtractor, build_context_document, draft_intake, open_questions,
                     read_conversations, record_from_json, rejection_summary, revise_intake,
                     write_drafted_intake)
from .llm import (MaterialityAssessor, ScenarioReviewer, ScenarioWriter, describe_graph,
                  describe_use_case)
from .llm.conversation_mapping import ConversationMapper
from .llm.reviewer import DEFAULT_PROPOSAL_LIMIT

logger = logging.getLogger("scenario_generator")


def build_scenarios(intake: IntakeData, with_probes: bool = False) -> List[Scenario]:
    """Deterministic scenario set from an intake — no LLM."""
    graph = DecisionGraph(intake.decisions, intake.states)
    walked, augmented = enumerate_paths(graph)
    scenarios = instantiate_all(walked, augmented, graph, intake.personas, intake.tools)
    if with_probes:
        scenarios += build_probes(intake)
    return scenarios


def build_graph(intake_path: str, graph_path: str, with_probes: bool = False) -> List[Scenario]:
    """Stage 1: intake -> decision graph -> scenario metadata + ground truth. No LLM."""
    intake = read_intake(intake_path)
    scenarios = build_scenarios(intake, with_probes)
    write_scenario_graph(graph_path, intake, scenarios)
    logger.info("Built %d scenarios for '%s' (%d probes). Wrote %s", len(scenarios), intake.name,
                sum(1 for s in scenarios if s.is_probe), graph_path)
    return scenarios


def build_probes_stage(intake_path: str, graph_path: str, output_path: str) -> List[Scenario]:
    """Append applicable probes to an existing graph file. Deterministic, no LLM.

    Pass the same path twice to update in place. Re-running replaces any probes already present
    rather than duplicating them, so the command is safe to repeat.
    """
    intake = read_intake(intake_path)
    existing = [s for s in read_scenarios(graph_path, intake) if not s.is_probe]
    probes = build_probes(intake)
    write_scenario_graph(output_path, intake, existing + probes)
    logger.info("Added %d probes to %d scenarios. Wrote %s",
                len(probes), len(existing), output_path)
    return existing + probes


def refine(intake_path: str, graph_path: str, output_prefix: str, writer=None,
           context_path: str = None, notes=None) -> List[Scenario]:
    """Stage 2: graph file -> LLM description and turn plan -> registry.

    Category and persona are already fixed deterministically by build-graph; materiality is not
    assessed here -- run ``assess_materiality`` next, then ``review``, then ``build_pack``.

    No challenge pack is written here. It used to be, and it was wrong every time: the pack's
    requested run counts come from materiality, which at this point is the untouched default on
    every scenario, so what it wrote said "run each of these three times" and was superseded by
    the next command in the sequence. A workbook that is stale the moment it is written is worse
    than one that does not exist, because only the second is obviously missing. The pack is built
    from the registry's final state -- see :func:`build_pack`, which exists for exactly this.
    """
    intake = read_intake(intake_path)
    scenarios = read_scenarios(graph_path, intake)

    (writer or ScenarioWriter(context=load_context(context_path, notes))).write(scenarios, intake)

    write_registry(f"{output_prefix}_registry.xlsx", intake, scenarios)
    logger.info("Wrote %s_registry.xlsx. Assess materiality and review before building the pack.",
                output_prefix)
    return scenarios


def assess_materiality(intake_path: str, registry_in_path: str, registry_out_path: str,
                       assessor: Optional[MaterialityAssessor] = None,
                       context_path: str = None, notes=None) -> List[Scenario]:
    """Stage: a separate LLM sweep that assigns materiality using cross-scenario signals, then
    rewrites the registry. Pass the same path twice to update in place."""
    intake = read_intake(intake_path)
    scenarios = read_scenarios(registry_in_path, intake)

    (assessor or MaterialityAssessor(context=load_context(context_path, notes))).assess(scenarios, intake)

    write_registry(registry_out_path, intake, scenarios)
    logger.info("Assessed materiality for %d scenarios. Wrote %s", len(scenarios), registry_out_path)
    return scenarios


def generate(intake_path: str, output_prefix: str, writer=None,
            assessor: Optional[MaterialityAssessor] = None, with_probes: bool = False,
            context_path: str = None, notes=None) -> List[Scenario]:
    """One-shot convenience: build_scenarios + LLM writer + materiality sweep + both workbooks,
    no intermediate files."""
    intake = read_intake(intake_path)
    scenarios = build_scenarios(intake, with_probes)
    context = load_context(context_path, notes)
    logger.info("Generated %d scenarios for '%s' (%d probes)", len(scenarios), intake.name,
                sum(1 for s in scenarios if s.is_probe))

    (writer or ScenarioWriter(context=context)).write(scenarios, intake)
    (assessor or MaterialityAssessor(context=context)).assess(scenarios, intake)

    write_challenge_pack(f"{output_prefix}_challenge_pack.xlsx", intake, scenarios)
    write_registry(f"{output_prefix}_registry.xlsx", intake, scenarios)
    logger.info("Wrote %s_challenge_pack.xlsx and %s_registry.xlsx. The pack reflects a benchmark "
                "that has not been reviewed; run review and rebuild it before issuing.",
                output_prefix, output_prefix)
    return scenarios


def review(intake_path: str, registry_in_path: str, registry_out_path: str,
           reviewer=None, context_path: str = None, notes=None, proposal_limit: int = None,
           owner_scenarios_path: str = None, pack_path: str = None) -> List[Scenario]:
    """Final stage: a whole-registry LLM sweep that may revise materiality and propose additions.

    Runs last, once every other pass has populated the registry — it is the only pass that sees
    the complete picture, so it settles materiality with the whole set and the deterministic
    redundancy evidence in view. Its verdict lands in its own columns rather than overwriting an
    earlier assessment, and proposals arrive with origin "llm-proposed" so they are never mistaken
    for graph-derived scenarios. Pass the same path twice to update in place.

    Supplying the owner's own scenario library is optional; where given, it is shown as context
    so the review can see where the owner's attention already went.
    """
    intake = read_intake(intake_path)
    scenarios = read_scenarios(registry_in_path, intake)
    existing = [s for s in scenarios if not s.is_proposed]

    reviewer = reviewer or ScenarioReviewer(
        context=load_context(context_path, notes),
        proposal_limit=proposal_limit or DEFAULT_PROPOSAL_LIMIT)
    owner = read_owner_scenarios(owner_scenarios_path) if owner_scenarios_path else None
    reviewed, proposals = reviewer.review(existing, intake, owner)

    revised = sum(1 for s in reviewed if s.review_materiality
                  and s.review_materiality != s.materiality)
    flagged = sum(1 for s in reviewed if s.review_flag)

    scenarios = reviewed + proposals
    write_registry(registry_out_path, intake, scenarios)
    logger.info("Reviewed %d scenarios: %d materiality revision(s), %d flagged, "
                "%d new scenario(s) proposed. Wrote %s",
                len(reviewed), revised, flagged, len(proposals), registry_out_path)

    if pack_path:
        write_challenge_pack(pack_path, intake, scenarios)
        logger.info("Rebuilt the challenge pack at %s.", pack_path)
    elif revised or proposals:
        logger.warning(
            "The review changed the benchmark, so any challenge pack written earlier is now out "
            "of date. Rebuild it with: build-pack <intake> %s <pack.xlsx>", registry_out_path)
    return scenarios


def build_pack(intake_path: str, registry_path: str, pack_path: str) -> List[Scenario]:
    """Write the challenge pack from a registry. No LLM.

    Kept separate because the pack depends on the registry's final state: materiality drives the
    requested run count, and the review pass can change it or add scenarios. Rebuilding after
    every registry change is what keeps the issued workbook and the answer key in step.
    """
    intake = read_intake(intake_path)
    scenarios = read_scenarios(registry_path, intake)
    write_challenge_pack(pack_path, intake, scenarios)
    logger.info("Wrote %s from %d scenarios.", pack_path, len(scenarios))
    return scenarios


# --------------------------------------------------------------------------- results

@dataclass
class IngestResult:
    """What ingestion produced, and where it put it."""

    record: object
    questions: List[dict]
    evidence_path: str
    context_path: str
    questions_path: str

    @property
    def summary(self) -> dict:
        from .core.evidence import summarise
        return summarise(self.record)


# --------------------------------------------------------------------------- stage 0

def ingest_documents(source_paths: Sequence[str], output_prefix: str,
                     extractor: Optional[DocumentExtractor] = None,
                     progress: Optional[Callable[[str], None]] = None,
                     resolve_passes: Optional[int] = None, cancel=None,
                     should_redact: Optional[Callable[[str], bool]] = None) -> IngestResult:
    """Stage 0: read submitted documents into verified evidence, context and open questions.

    Writes three files under ``output_prefix``: the evidence record, the cited context document
    that later stages take through ``--context``, and the questions nobody's documents answered.
    Every claim in the record was checked against the passage it cites; anything unsupported was
    discarded before it got here. Nothing is written if ``cancel`` interrupts the read: a stopped
    stage leaves the workspace exactly where it was before this run started.

    ``should_redact`` names which submitted files must be redacted regardless of the global
    ``PII_REDACTION`` setting -- the interface's per-file toggle, threaded down to where the
    documents are actually read. See :mod:`scenario_generator.ingest.redaction`.
    """
    extractor = extractor or DocumentExtractor(progress=progress, resolve_passes=resolve_passes,
                                               cancel=cancel, should_redact=should_redact)
    record = extractor.run([Path(p) for p in source_paths])

    evidence_path = f"{output_prefix}_evidence.json"
    context_path = f"{output_prefix}_context.md"
    questions_path = f"{output_prefix}_questions.md"

    Path(evidence_path).write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
    Path(context_path).write_text(build_context_document(record, Path(output_prefix).name),
                                  encoding="utf-8")

    questions = open_questions(record)
    Path(questions_path).write_text(render_questions(questions), encoding="utf-8")

    counts = record.to_dict()
    logger.info("Read %d document(s): %d statement(s) kept, %d question(s) outstanding. %s",
                len(counts["documents"]), len(record.usable()), len(questions),
                rejection_summary(record))
    logger.info("Wrote %s, %s and %s", evidence_path, context_path, questions_path)

    return IngestResult(record=record, questions=questions, evidence_path=evidence_path,
                        context_path=context_path, questions_path=questions_path)


def render_questions(questions: List[dict]) -> str:
    """The open questions as a document someone can work through and answer."""
    lines = ["# Open questions", "",
             "Answers become evidence attributed to the validation team rather than to a "
             "document. Supply them with --note, or in the interface.", ""]
    for number, question in enumerate(questions, start=1):
        lines.append(f"{number}. **{question['heading']}** — {question['question']}")
        lines.append(f"   _{question['detail']}_")
        lines.append("")
    if not questions:
        lines.append("Nothing outstanding: every category was addressed and every statement was "
                     "checkable.")
    return "\n".join(lines)


def draft_intake_workbook(context_path: str, output_path: str,
                          complete: Optional[Callable[..., str]] = None,
                          evidence_path: Optional[str] = None,
                          notes=None) -> "DraftResult":
    """Draft an intake workbook from an ingested context document.

    The result is a real intake in the shape ``init-template`` produces, plus a "Review This"
    sheet saying where the draft is weak. It is a starting point for a person to correct, not an
    authority -- but correcting a draft is an afternoon and writing one is a week.

    ``evidence_path`` is the record ingestion wrote beside the context document. It is read for
    one thing: the decision graph pulled out of any submitted workflow diagrams, which is handed
    to the drafter as structure rather than only as the prose rendering in the context file. A
    diagram is often the only place a branch is drawn, and confirming a graph is a far more
    reliable job than rebuilding one from sentences about a graph.

    ``notes`` is anything typed alongside the documents -- a correction, an answer to one of the
    open questions the first reading left, a constraint nobody wrote down. Every other pass this
    tool makes takes the same kind of input (see :func:`.core.context.load_context`), and the
    draft is the one pass a gap here would hurt most: it is the one call that decides the whole
    shape of the intake, and an answer given after ingestion but never passed to this one call
    would otherwise be redrafted every time this step re-runs.
    """
    context = load_context(context_path, notes)
    structure = _diagram_structure(evidence_path or _evidence_beside(context_path))
    draft = draft_intake(context, complete=complete, structure=structure)
    write_drafted_intake(Path(output_path), draft)

    counts = draft.counts()
    logger.info("Drafted an intake: %d capabilities, %d decision points, %d states, %d personas, "
                "%d tools.", counts["capabilities"], counts["decisions"], counts["states"],
                counts["personas"], counts["tools"])
    logger.info("%d point(s) flagged for review. Wrote %s",
                len(draft.review_notes), output_path)
    return DraftResult(draft=draft, path=output_path)


def revise_intake_workbook(current_path: str, output_path: str, context_path: str = None,
                           complete: Optional[Callable[..., str]] = None,
                           evidence_path: Optional[str] = None, notes=None) -> "DraftResult":
    """Revise an intake workbook in place, given what has been added since it was last written.

    The counterpart to :func:`draft_intake_workbook` for a declaration that already exists --
    drafted earlier, corrected by hand, or both. Re-running ``draft_intake_workbook`` would
    silently discard any hand correction, because a fresh draft has no way to know one was ever
    made; this reads ``current_path`` and hands the whole declaration to the model as what to
    revise rather than what to replace, with instructions to change only what the new context and
    notes actually require. See :func:`ingest.drafting.revise_intake`.

    ``output_path`` may be the same file as ``current_path`` -- the usual case, an intake revised
    where it stands -- or a different one, for a caller that wants to keep the prior version.
    """
    current = read_intake(current_path)
    rendered = f"{describe_use_case(current)}\n\n{describe_graph(current)}"

    context = load_context(context_path, notes)
    structure = _diagram_structure(evidence_path or _evidence_beside(context_path or ""))
    revision = revise_intake(context, rendered, complete=complete, structure=structure)
    write_drafted_intake(Path(output_path), revision)

    counts = revision.counts()
    logger.info("Revised the intake: %d capabilities, %d decision points, %d states, %d "
                "personas, %d tools.", counts["capabilities"], counts["decisions"],
                counts["states"], counts["personas"], counts["tools"])
    logger.info("%d point(s) still flagged for review. Wrote %s",
                len(revision.review_notes), output_path)
    return DraftResult(draft=revision, path=output_path)


def _evidence_beside(context_path: str) -> Optional[str]:
    """The evidence record ingest wrote alongside this context document, if it is still there.

    ``ingest`` writes ``<prefix>_context.md`` and ``<prefix>_evidence.json`` together, so the one
    can be found from the other and nobody has to pass both on the command line.
    """
    context = Path(context_path)
    if not context.name.endswith("_context.md"):
        return None
    candidate = context.with_name(context.name[: -len("_context.md")] + "_evidence.json")
    return str(candidate) if candidate.exists() else None


def _diagram_structure(evidence_path: Optional[str]) -> Optional[dict]:
    """The workflow read from submitted diagrams, where ingestion recorded one."""
    if not evidence_path or not Path(evidence_path).exists():
        return None
    try:
        return record_from_json(Path(evidence_path)).structure or None
    except (OSError, ValueError) as exc:
        logger.warning("Could not read the evidence record for the diagram structure: %s", exc)
        return None


@dataclass
class DraftResult:
    draft: object
    path: str

    @property
    def counts(self) -> dict:
        return self.draft.counts()


@dataclass
class ConversationCoverageResult:
    """What the team's conversations turned out to cover."""

    report: object
    mappings: list
    how_read: str
    report_path: str

    @property
    def summary(self) -> dict:
        return self.report.summary()


def map_conversation_coverage(intake_path: str, registry_path: str, conversations_path: str,
                              report_path: str, mapper: Optional[ConversationMapper] = None,
                              threshold: int = DEFAULT_THRESHOLD, annotate_registry: bool = True,
                              progress=None, cancel=None) -> ConversationCoverageResult:
    """Stage: map submitted conversations onto the benchmark and count what they cover.

    The benchmark is read from the registry, so each scenario's category and materiality are
    whatever the earlier stages assigned -- this never recomputes them. What it adds is volume:
    how many of the team's conversations landed on each scenario, which of them landed on nothing,
    and whether any grouping the team applied agrees with where the conversations actually went.

    ``threshold`` is the line between represented and under-represented. It is a caller's
    judgement rather than a property of the data -- see :mod:`.core.representation`.
    """
    intake = read_intake(intake_path)
    benchmark = read_registry(registry_path)
    texts = {s.id: s.description for s in read_scenarios(registry_path, intake)}

    conversations, how = read_conversations(Path(conversations_path))
    mapper = mapper or ConversationMapper(progress=progress, cancel=cancel)
    mappings = mapper.map(conversations, benchmark, intake, texts)

    report = build_report(mappings, benchmark, threshold=threshold)
    write_coverage_report(report_path, report, mappings, texts)
    if annotate_registry:
        annotate_coverage(registry_path, intake, report)

    counts = report.summary()
    logger.info("%d conversation(s) covered %d of %d scenarios at a threshold of %d; "
                "%d never exercised, %d matched nothing. Wrote %s",
                counts["Conversations read"], counts["Represented"], counts["Benchmark scenarios"],
                threshold, counts["Never exercised"], counts["Matched no scenario"], report_path)
    return ConversationCoverageResult(report=report, mappings=mappings, how_read=how,
                                      report_path=report_path)


def annotate_coverage(registry_path: str, intake: IntakeData, report) -> int:
    """Record against each scenario how much of the team's own testing landed on it.

    An annotation, not a filter. A well-covered scenario stays in the registry and, unless someone
    asks otherwise, in the pack: whether running it again is duplicated effort or independent
    confirmation depends on how far their testing is trusted, and that is a judgement for the
    person issuing the pack rather than for this tool. What the tool can do is put the count in
    front of them.

    The columns never reach the challenge pack -- see :func:`io.write_challenge_pack`. Telling the
    modelling team which scenarios the validation team already considers answered would tell them
    exactly which ones to concentrate on.
    """
    scenarios = read_scenarios(registry_path, intake)
    counts = {entry.scenario.id: entry for entry in report.scenarios}

    annotated = 0
    for scenario in scenarios:
        entry = counts.get(scenario.id)
        if entry is None or not entry.count:
            continue
        plural = "" if entry.count == 1 else "s"
        scenario.owner_coverage = f"{entry.count} conversation{plural}"
        scenario.owner_coverage_note = ", ".join(
            filter(None, [entry.confidence_summary, ", ".join(entry.conversation_ids)]))
        annotated += 1

    write_registry(registry_path, intake, scenarios)
    logger.info("Annotated %d scenario(s) with what their conversations cover. Nothing was "
                "removed -- whether to drop a covered scenario is your call.", annotated)
    return annotated
