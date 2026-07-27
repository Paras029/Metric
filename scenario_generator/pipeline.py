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
                   instantiate_all, load_context, match_scenarios, read_intake,
                   read_owner_scenarios)
from .io import (read_registry, read_scenarios, write_challenge_pack, write_overlap_report,
                write_registry, write_scenario_graph)
from .llm import (MaterialityAssessor, MetadataExtractor, ScenarioReviewer,
                  ScenarioWriter)
from .llm.reviewer import DEFAULT_PROPOSAL_LIMIT
from .ingest import (DocumentExtractor, build_context_document, draft_intake, open_questions,
                     read_owner_library, rejection_summary, write_drafted_intake)

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
    """Stage 2: graph file -> LLM description and turn plan -> challenge pack + registry.

    Category and persona are already fixed deterministically by build-graph; materiality is not
    assessed here — run `assess_materiality` next.
    """
    intake = read_intake(intake_path)
    scenarios = read_scenarios(graph_path, intake)

    (writer or ScenarioWriter(context=load_context(context_path, notes))).write(scenarios, intake)

    write_challenge_pack(f"{output_prefix}_challenge_pack.xlsx", intake, scenarios)
    write_registry(f"{output_prefix}_registry.xlsx", intake, scenarios)
    logger.info("Wrote %s_challenge_pack.xlsx and %s_registry.xlsx", output_prefix, output_prefix)
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
    logger.info("Wrote %s_challenge_pack.xlsx and %s_registry.xlsx", output_prefix, output_prefix)
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


def map_coverage(intake_path: str, registry_path: str, owner_path: str, report_path: str,
                 extractor: Optional[MetadataExtractor] = None) -> "CoverageResult":
    """Stage: map a modeling team's own scenarios onto a generated registry, write the overlap
    report. The benchmark is loaded from the registry, so its category and materiality are
    whatever refine()/assess_materiality() already assigned — coverage never recomputes them.
    """
    intake = read_intake(intake_path)
    benchmark = read_registry(registry_path)

    # Read whatever shape the owner sent rather than demanding one layout; how it was read is
    # logged, because a misread column is the kind of thing that quietly halves a coverage figure.
    owner_scenarios, how = read_owner_library(Path(owner_path))
    logger.info("Read %d scenario(s) from %s (%s).",
                len(owner_scenarios), Path(owner_path).name, how)

    extractor = extractor or MetadataExtractor()
    default_persona = next((p.id for p in intake.personas if p.is_default), intake.personas[0].id)
    matches = match_scenarios(owner_scenarios, extractor.extract(owner_scenarios, intake),
                              benchmark, default_persona)
    covered, gaps = write_overlap_report(report_path, intake, matches, benchmark)
    logger.info("Owner covered %d/%d benchmark scenarios; %d gap(s). Wrote %s",
                covered, len(benchmark), gaps, report_path)
    return CoverageResult(covered=covered, gaps=gaps, benchmark=len(benchmark),
                          owner_scenarios=len(owner_scenarios), report_path=report_path,
                          how_read=how)


# --------------------------------------------------------------------------- results

@dataclass
class CoverageResult:
    """What map_coverage established, so a caller need not re-read the workbook it just wrote."""

    covered: int
    gaps: int
    benchmark: int
    owner_scenarios: int
    report_path: str
    how_read: str = ""


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
                     progress: Optional[Callable[[str], None]] = None) -> IngestResult:
    """Stage 0: read submitted documents into verified evidence, context and open questions.

    Writes three files under ``output_prefix``: the evidence record, the cited context document
    that later stages take through ``--context``, and the questions nobody's documents answered.
    Every claim in the record was checked against the passage it cites; anything unsupported was
    discarded before it got here.
    """
    extractor = extractor or DocumentExtractor(progress=progress)
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
                          complete: Optional[Callable[..., str]] = None) -> "DraftResult":
    """Draft an intake workbook from an ingested context document.

    The result is a real intake in the shape ``init-template`` produces, plus a "Review This"
    sheet saying where the draft is weak. It is a starting point for a person to correct, not an
    authority -- but correcting a draft is an afternoon and writing one is a week.
    """
    context = Path(context_path).read_text(encoding="utf-8")
    draft = draft_intake(context, complete=complete)
    write_drafted_intake(Path(output_path), draft)

    counts = draft.counts()
    logger.info("Drafted an intake: %d capabilities, %d decision points, %d states, %d personas, "
                "%d tools.", counts["capabilities"], counts["decisions"], counts["states"],
                counts["personas"], counts["tools"])
    logger.info("%d point(s) flagged for review. Wrote %s",
                len(draft.review_notes), output_path)
    return DraftResult(draft=draft, path=output_path)


@dataclass
class DraftResult:
    draft: object
    path: str

    @property
    def counts(self) -> dict:
        return self.draft.counts()
