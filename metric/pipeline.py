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

from metric.domain import (DecisionGraph, IntakeData, Scenario, enumerate_by_span,
                           enumerate_paths, read_intake, read_owner_scenarios, read_scenarios,
                           read_space_metadata, write_coverage_report, write_data_template,
                           write_scenario_graph, write_space_metadata)
from metric.llm import describe_enumeration, describe_graph, describe_use_case
from metric.phases.coverage.coverage.mapping import ConversationMapper
from metric.phases.intake.intake import (DocumentExtractor, DraftedIntake, build_context_document,
                                         carry_forward, draft_intake, open_questions,
                                         record_from_json, reconcile_intake, rejection_summary,
                                         repair_intake, revise_intake, write_drafted_intake)
from metric.phases.coverage.coverage import read_conversations, redact_conversations
from metric.phases.intake.intake.context import load_context as _load_context
from metric.phases.intake.intake.extraction import SPLIT_ACROSS_IMAGES
from metric.phases.intake.intake.gaps import find_gaps
from metric.phases.intake.intake.representation import DEFAULT_THRESHOLD, build_report
from metric.phases.scenario_generator.materiality.assess import MaterialityAssessor
from metric.phases.scenario_generator.review.reviewer import (DEFAULT_PROPOSAL_LIMIT,
                                                              ScenarioReviewer)
from metric.phases.scenario_generator.scenarios.writer import ScenarioWriter
from metric.phases.scenario_generator.workflow.generation import (instantiate_all, 
                                                                  instantiate_span,
                                                                  number_scenarios)
from metric.phases.scenario_generator.workflow.probes import build_probes

logger = logging.getLogger("metric")


def load_context(path: str = None, notes=None) -> str:
    """The supplementary context every model-using pass takes, under the configured budget."""
    from metric.llm import config

    return _load_context(path, notes, max_chars=config.MAX_CONTEXT_CHARS)


def build_scenario_space(intake: IntakeData, with_probes: bool = False,
                         limits=None) -> List[Scenario]:
    """Deterministic scenario set from an intake — no LLM."""
    graph = DecisionGraph(intake.decisions, intake.states)
    scenarios: List[Scenario] = []
    for span, walked, augmented in enumerate_by_span(graph, intake.capabilities, limits):
        scenarios += instantiate_span(span, walked, augmented, graph, intake.personas,
                                      intake.tools)
    number_scenarios(scenarios)
    if with_probes:
        scenarios += build_probes(intake)
    return scenarios


def build_graph(intake_path: str, graph_path: str, with_probes: bool = False) -> List[Scenario]:
    """Stage 1: intake -> decision graph -> scenario metadata + ground truth. No LLM."""
    intake = read_intake(intake_path)
    scenarios = build_scenario_space(intake, with_probes)
    write_scenario_graph(graph_path, intake, scenarios)
    logger.info("Built %d scenarios for '%s' (%d probes). Wrote %s", len(scenarios), intake.name,
                sum(1 for s in scenarios if s.is_probe), graph_path)
    return scenarios


def build_probes_stage(intake_path: str, graph_path: str, output_path: str) -> List[Scenario]:
    """Append applicable probes to an existing graph file. Deterministic, no LLM."""
    intake = read_intake(intake_path)
    existing = [s for s in read_scenarios(graph_path, intake) if not s.is_probe]
    probes = build_probes(intake)
    write_scenario_graph(output_path, intake, existing + probes)
    logger.info("Added %d probes to %d scenarios. Wrote %s",
                len(probes), len(existing), output_path)
    return existing + probes


def refine(intake_path: str, graph_path: str, output_prefix: str, writer=None,
           context_path: str = None, notes=None) -> List[Scenario]:
    """Stage 2: graph file -> LLM description and turn plan -> the metadata workbook."""
    intake = read_intake(intake_path)
    scenarios = read_scenarios(graph_path, intake)

    (writer or ScenarioWriter(context=load_context(context_path, notes))).write(scenarios, intake)

    write_space_metadata(f"{output_prefix}_scenario_space_metadata.xlsx", intake, scenarios)
    logger.info("Wrote %s_scenario_space_metadata.xlsx. Assess materiality and review before building the pack.",
                output_prefix)
    return scenarios


def assess_materiality(intake_path: str, registry_in_path: str, registry_out_path: str,
                       assessor: Optional[MaterialityAssessor] = None,
                       context_path: str = None, notes=None) -> List[Scenario]:
    """Stage: a separate LLM sweep that assigns materiality using cross-scenario signals, then
    rewrites the scenario space metadata. Pass the same path twice to update in place."""
    intake = read_intake(intake_path)
    scenarios = read_scenarios(registry_in_path, intake)

    (assessor or MaterialityAssessor(context=load_context(context_path, notes))).assess(scenarios, intake)

    write_space_metadata(registry_out_path, intake, scenarios)
    logger.info("Assessed materiality for %d scenarios. Wrote %s", len(scenarios), registry_out_path)
    return scenarios


def generate(intake_path: str, output_prefix: str, writer=None,
            assessor: Optional[MaterialityAssessor] = None, with_probes: bool = False,
            context_path: str = None, notes=None) -> List[Scenario]:
    """One-shot convenience: build_scenario_space + LLM writer + materiality sweep + both workbooks,
    no intermediate files."""
    intake = read_intake(intake_path)
    scenarios = build_scenario_space(intake, with_probes)
    context = load_context(context_path, notes)
    logger.info("Generated %d scenarios for '%s' (%d probes)", len(scenarios), intake.name,
                sum(1 for s in scenarios if s.is_probe))

    (writer or ScenarioWriter(context=context)).write(scenarios, intake)
    (assessor or MaterialityAssessor(context=context)).assess(scenarios, intake)

    write_data_template(f"{output_prefix}_data_template.xlsx", intake, scenarios)
    write_space_metadata(f"{output_prefix}_scenario_space_metadata.xlsx", intake, scenarios)
    logger.info("Wrote %s_data_template.xlsx and %s_scenario_space_metadata.xlsx. The pack reflects a scenario space "
                "that has not been reviewed; run review and rebuild it before issuing.",
                output_prefix, output_prefix)
    return scenarios


def review(intake_path: str, registry_in_path: str, registry_out_path: str,
           reviewer=None, context_path: str = None, notes=None, proposal_limit: int = None,
           owner_scenarios_path: str = None, pack_path: str = None) -> List[Scenario]:
    """Final stage: a whole-space LLM sweep that may revise materiality and propose additions."""
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
    write_space_metadata(registry_out_path, intake, scenarios)
    logger.info("Reviewed %d scenarios: %d materiality revision(s), %d flagged, "
                "%d new scenario(s) proposed. Wrote %s",
                len(reviewed), revised, flagged, len(proposals), registry_out_path)

    if pack_path:
        write_data_template(pack_path, intake, scenarios)
        logger.info("Rebuilt the data template at %s.", pack_path)
    elif revised or proposals:
        logger.warning(
            "The review changed the space, so any data template written earlier is now out "
            "of date. Rebuild it with: build-data-template <intake> %s <template.xlsx>",
            registry_out_path)
    return scenarios


def build_pack(intake_path: str, metadata_path: str, pack_path: str) -> List[Scenario]:
    """Write the data template from a metadata workbook. No LLM."""
    intake = read_intake(intake_path)
    scenarios = read_scenarios(metadata_path, intake)
    write_data_template(pack_path, intake, scenarios)
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
        from metric.phases.intake.intake.evidence import summarise
        return summarise(self.record)


# --------------------------------------------------------------------------- stage 0

def ingest_documents(source_paths: Sequence[str], output_prefix: str,
                     extractor: Optional[DocumentExtractor] = None,
                     progress: Optional[Callable[[str], None]] = None,
                     resolve_passes: Optional[int] = None, cancel=None,
                     should_redact: Optional[Callable[[str], bool]] = None,
                     diagram_mode: str = SPLIT_ACROSS_IMAGES) -> IngestResult:
    """Stage 0: read submitted documents into verified evidence, context and open questions."""
    extractor = extractor or DocumentExtractor(progress=progress, resolve_passes=resolve_passes,
                                               cancel=cancel, should_redact=should_redact,
                                               diagram_mode=diagram_mode)
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
             "Answers become evidence attributed to the validator rather than to a "
             "document. Supply them with --note, or in the interface.", ""]
    for number, question in enumerate(questions, start=1):
        lines.append(f"{number}. **{question['heading']}** — {question['question']}")
        lines.append(f"   _{question['detail']}_")
        lines.append("")
    if not questions:
        lines.append("Nothing outstanding: every category was addressed and every statement was "
                     "checkable.")
    return "\n".join(lines)


def structural_problems(intake: IntakeData) -> List[str]:
    """What the declaration is missing, as lines a repair pass can be given."""
    return [f"{gap.heading}: {gap.question} ({gap.why})" for gap in find_gaps(intake)]


def draft_intake_workbook(context_path: str, output_path: str,
                          complete: Optional[Callable[..., str]] = None,
                          evidence_path: Optional[str] = None,
                          notes=None, progress=None, repair: bool = True,
                          reconcile: bool = True) -> "DraftResult":
    """Draft an intake workbook from an ingested context document."""
    report = progress or (lambda *args, **kwargs: None)
    context = load_context(context_path, notes)
    structure = _diagram_structure(evidence_path or _evidence_beside(context_path))

    report("Drafting the intake", 0, 2)
    draft = draft_intake(context, complete=complete, structure=structure)
    write_drafted_intake(Path(output_path), draft)
    report("Drafted the intake", 1, 2)

    counts = draft.counts()
    logger.info("Drafted an intake: %d capabilities, %d decision points, %d states, %d personas, "
                "%d tools.", counts["capabilities"], counts["decisions"], counts["states"],
                counts["personas"], counts["tools"])

    if repair:
        draft = _repair_draft(output_path, context, structure, complete, draft, report)
    if reconcile:
        draft = _reconcile_draft(output_path, context, structure, complete, draft, report)

    report("Finished the intake", 2, 2)
    logger.info("%d point(s) flagged for review. Wrote %s",
                len(draft.review_notes), output_path)
    return DraftResult(draft=draft, path=output_path)


def _repair_draft(output_path: str, context: str, structure, complete, draft, report):
    """Read the draft back, and put whatever it left structurally incomplete to the model once."""
    try:
        current = read_intake(output_path)
    except Exception as exc:                               # an unreadable draft is its own problem
        logger.warning("Could not read the draft back to check it: %s", exc)
        return draft

    problems = structural_problems(current)
    if not problems:
        return draft

    report(f"Filling in {len(problems)} gap(s) the draft left", 1, 2)
    logger.info("The draft left %d structural gap(s); putting them back to the documents.",
                len(problems))

    rendered = f"{describe_use_case(current)}\n\n{describe_graph(current)}"
    repaired = repair_intake(context, rendered, problems, complete=complete, structure=structure,
                             enumeration=describe_enumeration(current))
    if repaired is None:
        return draft

    merged = DraftedIntake(carry_forward(draft.data, repaired.data))
    settled = _accept_if_better(output_path, draft, merged, len(problems))
    return settled


def _reconcile_draft(output_path: str, context: str, structure, complete, draft, report):
    """Read the whole declaration back once more and correct what only the whole reveals."""
    try:
        current = read_intake(output_path)
    except Exception as exc:
        logger.warning("Could not read the declaration back to reconcile it: %s", exc)
        return draft

    report("Reading the declaration back as a whole", 1, 2)
    rendered = f"{describe_use_case(current)}\n\n{describe_graph(current)}"
    reconciled = reconcile_intake(context, rendered, complete=complete, structure=structure,
                                  enumeration=describe_enumeration(current))
    if reconciled is None:
        return draft

    merged = DraftedIntake(carry_forward(draft.data, reconciled.data))
    return _accept_if_better(output_path, draft, merged, len(structural_problems(current)))


def _accept_if_better(output_path: str, draft, candidate, before: int):
    """Write ``candidate`` only if it audits better than the draft already on disk."""
    scratch = Path(output_path).with_suffix(".candidate.xlsx")
    try:
        write_drafted_intake(scratch, candidate)
        after = len(structural_problems(read_intake(str(scratch))))
    except Exception as exc:
        logger.warning("The second look could not be read back, so the draft stands: %s", exc)
        return draft
    finally:
        scratch.unlink(missing_ok=True)

    if after > before:
        logger.warning("The second look left %d gap(s) where the draft had %d, so the draft "
                       "stands.", after, before)
        return draft

    write_drafted_intake(Path(output_path), candidate)
    logger.info("After looking again: %d of %d gap(s) filled in.", before - after, before)
    return candidate


def revise_intake_workbook(current_path: str, output_path: str, context_path: str = None,
                           complete: Optional[Callable[..., str]] = None,
                           evidence_path: Optional[str] = None, notes=None,
                           progress=None, repair: bool = True,
                           reconcile: bool = True) -> "DraftResult":
    """Revise an intake workbook in place, given what has been added since it was last written."""
    report = progress or (lambda *args, **kwargs: None)
    current = read_intake(current_path)
    rendered = f"{describe_use_case(current)}\n\n{describe_graph(current)}"
    before = len(structural_problems(current))

    context = load_context(context_path, notes)
    structure = _diagram_structure(evidence_path or _evidence_beside(context_path or ""))

    report("Revising the declaration", 0, 2)
    revision = revise_intake(context, rendered, complete=complete, structure=structure)

    # A revision that came back emptier than what it was revising is not a revision. The model was
    # asked to carry everything forward and change only what the new information requires; a reply
    # that dropped most of the graph did not do that, and writing it would lose work a person may
    # have spent an afternoon on. Kept as it was, and said aloud.
    if _is_thinner(revision, current):
        logger.warning(
            "The revision came back with %d decision(s) and %d state(s) against %d and %d in the "
            "declaration it was revising, so it was discarded and the current intake kept. Run it "
            "again, or revise the workbook by hand.",
            len(revision.data["decisions"]), len(revision.data["states"]),
            len(current.decisions), len(current.states))
        report("Kept the current declaration", 2, 2)
        return DraftResult(draft=revision, path=current_path)

    write_drafted_intake(Path(output_path), revision)
    report("Revised the declaration", 1, 2)

    counts = revision.counts()
    logger.info("Revised the intake: %d capabilities, %d decision points, %d states, %d "
                "personas, %d tools.", counts["capabilities"], counts["decisions"],
                counts["states"], counts["personas"], counts["tools"])

    if repair:
        revision = _repair_draft(output_path, context, structure, complete, revision, report)
    if reconcile:
        revision = _reconcile_draft(output_path, context, structure, complete, revision, report)

    after = len(structural_problems(read_intake(output_path)))
    logger.info("%d structural gap(s) before, %d after. %d point(s) flagged for review. Wrote %s",
                before, after, len(revision.review_notes), output_path)
    report("Finished the intake", 2, 2)
    return DraftResult(draft=revision, path=output_path)


# How much smaller a revision may be before it is treated as a failed revision rather than as a
# deliberate simplification. A revision does remove things sometimes -- two decisions merged, a
# persona that was the same objective twice -- so this is not "no smaller"; it is "not collapsed".
_THINNING_LIMIT = 0.6


def _is_thinner(revision, current: IntakeData) -> bool:
    """Whether a revision dropped so much of the graph that it cannot be a revision of it."""
    for got, had in ((len(revision.data["decisions"]), len(current.decisions)),
                     (len(revision.data["states"]), len(current.states))):
        if had and got < had * _THINNING_LIMIT:
            return True
    return False


def _evidence_beside(context_path: str) -> Optional[str]:
    """The evidence record ingest wrote alongside this context document, if it is still there."""
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
    """What the model owner's conversations turned out to cover."""

    report: object
    mappings: list
    how_read: str
    report_path: str

    @property
    def summary(self) -> dict:
        return self.report.summary()


def map_conversation_coverage(intake_path: str, metadata_path: str, conversations_path: str,
                              report_path: str, mapper: Optional[ConversationMapper] = None,
                              threshold: int = DEFAULT_THRESHOLD, annotate_registry: bool = True,
                              redact: bool = False, complete=None,
                              progress=None, cancel=None) -> ConversationCoverageResult:
    """Stage: map submitted conversations onto the scenario space and count what they cover."""
    intake = read_intake(intake_path)
    space = read_space_metadata(metadata_path)
    texts = {s.id: s.description for s in read_scenarios(metadata_path, intake)}

    conversations, how = read_conversations(Path(conversations_path), complete=complete)
    redact_conversations(conversations, force=redact)
    mapper = mapper or ConversationMapper(progress=progress, cancel=cancel)
    # Mapped against everything that was *issued*, reported against the functional space. The
    # data template carries the probes as well, so a team that ran one and said so on the row they
    # were given must not be told it matched nothing -- that reads as a hole in their testing when
    # it is a hole in this reader. What coverage then *measures* is still the functional space
    # only, deliberately: a probe is a property of the agent rather than a route through it, and
    # counting probes in the denominator would move the figure without changing the evidence.
    mappings = mapper.map(conversations, space, intake, texts,
                          issued=read_space_metadata(metadata_path, functional_only=False))

    report = build_report(mappings, space, threshold=threshold)
    write_coverage_report(report_path, report, mappings, texts)
    if annotate_registry:
        annotate_coverage(metadata_path, intake, report)

    counts = report.summary()
    logger.info("%d conversation(s) covered %d of %d scenarios at a threshold of %d; "
                "%d never exercised, %d matched nothing. Wrote %s",
                counts["Conversations read"], counts["Represented"], counts["Scenarios in the space"],
                threshold, counts["Never exercised"], counts["Matched no scenario"], report_path)
    return ConversationCoverageResult(report=report, mappings=mappings, how_read=how,
                                      report_path=report_path)


def annotate_coverage(metadata_path: str, intake: IntakeData, report) -> int:
    """Record against each scenario how much of the model owner's testing landed on it."""
    scenarios = read_scenarios(metadata_path, intake)
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

    write_space_metadata(metadata_path, intake, scenarios)
    logger.info("Annotated %d scenario(s) with what their conversations cover. Nothing was "
                "removed -- dropping a covered scenario is a judgement call.", annotated)
    return annotated
