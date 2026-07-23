"""Orchestration for the five stages: build-graph, refine, assess-materiality, map-coverage,
and the one-shot `generate` convenience. No argparse here — see cli.py for the command line.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from .core import (DecisionGraph, IntakeData, Scenario, build_probes, enumerate_paths,
                   instantiate_all, load_context, match_scenarios, read_intake,
                   read_owner_scenarios)
from .io import (read_registry, read_scenarios, write_challenge_pack, write_overlap_report,
                write_registry, write_scenario_graph)
from .llm import (MaterialityAssessor, MetadataExtractor, ScenarioReviewer,
                  ScenarioWriter)
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
           context_path: str = None) -> List[Scenario]:
    """Stage 2: graph file -> LLM description and turn plan -> challenge pack + registry.

    Category and persona are already fixed deterministically by build-graph; materiality is not
    assessed here — run `assess_materiality` next.
    """
    intake = read_intake(intake_path)
    scenarios = read_scenarios(graph_path, intake)

    (writer or ScenarioWriter(context=load_context(context_path))).write(scenarios, intake)

    write_challenge_pack(f"{output_prefix}_challenge_pack.xlsx", intake, scenarios)
    write_registry(f"{output_prefix}_registry.xlsx", intake, scenarios)
    logger.info("Wrote %s_challenge_pack.xlsx and %s_registry.xlsx", output_prefix, output_prefix)
    return scenarios


def assess_materiality(intake_path: str, registry_in_path: str, registry_out_path: str,
                       assessor: Optional[MaterialityAssessor] = None,
                       context_path: str = None) -> List[Scenario]:
    """Stage: a separate LLM sweep that assigns materiality using cross-scenario signals, then
    rewrites the registry. Pass the same path twice to update in place."""
    intake = read_intake(intake_path)
    scenarios = read_scenarios(registry_in_path, intake)

    (assessor or MaterialityAssessor(context=load_context(context_path))).assess(scenarios, intake)

    write_registry(registry_out_path, intake, scenarios)
    logger.info("Assessed materiality for %d scenarios. Wrote %s", len(scenarios), registry_out_path)
    return scenarios


def generate(intake_path: str, output_prefix: str, writer=None,
            assessor: Optional[MaterialityAssessor] = None, with_probes: bool = False,
            context_path: str = None) -> List[Scenario]:
    """One-shot convenience: build_scenarios + LLM writer + materiality sweep + both workbooks,
    no intermediate files."""
    intake = read_intake(intake_path)
    scenarios = build_scenarios(intake, with_probes)
    context = load_context(context_path)
    logger.info("Generated %d scenarios for '%s' (%d probes)", len(scenarios), intake.name,
                sum(1 for s in scenarios if s.is_probe))

    (writer or ScenarioWriter(context=context)).write(scenarios, intake)
    (assessor or MaterialityAssessor(context=context)).assess(scenarios, intake)

    write_challenge_pack(f"{output_prefix}_challenge_pack.xlsx", intake, scenarios)
    write_registry(f"{output_prefix}_registry.xlsx", intake, scenarios)
    logger.info("Wrote %s_challenge_pack.xlsx and %s_registry.xlsx", output_prefix, output_prefix)
    return scenarios


def review(intake_path: str, registry_in_path: str, registry_out_path: str,
           reviewer=None, context_path: str = None, proposal_limit: int = None,
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
        context=load_context(context_path),
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
                 extractor: Optional[MetadataExtractor] = None) -> None:
    """Stage: map a modeling team's own scenarios onto a generated registry, write the overlap
    report. The benchmark is loaded from the registry, so its category and materiality are
    whatever refine()/assess_materiality() already assigned — coverage never recomputes them.
    """
    intake = read_intake(intake_path)
    benchmark = read_registry(registry_path)
    owner_scenarios = read_owner_scenarios(owner_path)

    extractor = extractor or MetadataExtractor()
    default_persona = next((p.id for p in intake.personas if p.is_default), intake.personas[0].id)
    matches = match_scenarios(owner_scenarios, extractor.extract(owner_scenarios, intake),
                              benchmark, default_persona)
    covered, gaps = write_overlap_report(report_path, intake, matches, benchmark)
    logger.info("Owner covered %d/%d benchmark scenarios; %d gap(s). Wrote %s",
                covered, len(benchmark), gaps, report_path)
