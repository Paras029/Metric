"""What each stage of the pipeline actually does, and where it puts the result.

One runner per stage. None of them contain pipeline logic: they read what the previous stage left
on disk, call the same functions the command line calls, and write their own output back. That is
what keeps the two front ends capable of the same things -- a workspace part-finished in the
interface can be finished from the command line, and the reverse.

Every runner has the same signature, ``(workspace, progress, cancel)``, whether or not it has
anything long to report or interrupt. A stage with nothing to interrupt finds the signal unset,
which costs nothing and is far easier to reason about than a list of which stages honour it.

Anything a runner raises is shown in that stage's panel rather than swallowed; anything it returns
is the set of figures shown beside it when it finishes.
"""
from __future__ import annotations

import logging
import shutil
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List

from ..core.evidence import FACETS
from ..core.generation import required_runs
from ..core.intake import (attach_decision_to_state, merge_decisions, read_intake,
                           set_state_reached_via)
from ..core.models import MATERIALITY, IntakeData
from ..ingest import record_from_json
from ..ingest.groups import evidence_files, owner_scenario_file
from ..ingest.owner_library import UnreadableLibrary
from ..io import read_scenarios, write_challenge_pack, write_registry
from ..llm import MaterialityAssessor, ScenarioReviewer, ScenarioWriter
from ..pipeline import (build_scenarios, draft_intake_workbook, ingest_documents, map_coverage)
from .workspace import Workspace

logger = logging.getLogger(__name__)

DRAFT_INTAKE = "drafted_intake.xlsx"


# --------------------------------------------------------------------------- stage runners
#
# Each runner takes the workspace, does one stage's work, and returns the figures worth showing.
# None of them contain pipeline logic: they read what the previous stage left on disk, call the
# same functions the command line calls, and write their own output back. Anything they raise is
# shown in the stage panel rather than swallowed.

REGISTRY = "registry.xlsx"
EVIDENCE = "ingest_evidence.json"
CONTEXT = "ingest_context.md"
PACK = "challenge_pack.xlsx"
OVERLAP = "coverage.xlsx"

# What each stage puts on disk, so clearing a stage can actually clear it. Listed against the
# stage that *creates* the file rather than every stage that rewrites it: clearing the scenario
# text does not mean throwing away the registry the benchmark stage built.
#
# Submitted documents appear nowhere here. They are input rather than output, and each already has
# its own remove -- deleting the pack because a later stage was re-run would be a rout.
STAGE_OUTPUTS: Dict[str, tuple] = {
    "documents": (EVIDENCE, CONTEXT, "ingest_questions.md"),
    "intake": (DRAFT_INTAKE,),
    "benchmark": (REGISTRY,),
    "coverage": (OVERLAP,),
    "issue": (PACK,),
}

# Each scenario stage also keeps a copy of the registry as it left it -- see _save_registry. They
# are listed against their own stage so that clearing one clears its snapshot with it, and a
# cleared stage stops showing a reading it is no longer claiming to have produced.
for _key in ("benchmark", "text", "materiality", "review", "coverage", "issue"):
    STAGE_OUTPUTS[_key] = STAGE_OUTPUTS.get(_key, ()) + (f"registry.{_key}.xlsx",)


def _snapshot(key: str) -> str:
    """What a stage's own copy of the registry is called."""
    return f"registry.{key}.xlsx"


def _save_registry(workspace: Workspace, key: str, intake: IntakeData, scenarios) -> None:
    """Write the live registry, and keep a copy of it as this stage left it.

    Every stage from the benchmark onward rewrites one registry, which means going back to an
    earlier stage's page would otherwise show what *later* stages have since made of it -- a
    scenario-text page listing tiers assigned after it ran, and proposals that did not exist.
    The snapshot is what lets each stage show its own reading. It is a copy of a file already
    being written rather than a second format, so nothing has to stay in step with it.
    """
    write_registry(str(workspace.root / REGISTRY), intake, scenarios)
    shutil.copyfile(workspace.root / REGISTRY, workspace.root / _snapshot(key))
    workspace.state(key).artifacts["registry"] = REGISTRY


def _intake(workspace: Workspace) -> IntakeData:
    path = workspace.artifact_path("intake", "workbook")
    if not path:
        raise ValueError("No intake workbook yet. Provide one at the intake stage.")
    return read_intake(str(path))


def _scenarios(workspace: Workspace, intake: IntakeData):
    """The benchmark as the last stage left it."""
    path = workspace.root / REGISTRY
    if not path.exists():
        raise ValueError("Build the benchmark first.")
    return read_scenarios(str(path), intake)


def _context(workspace: Workspace) -> str:
    """Everything available as supplementary context: the extracted document context, plus every
    note the user has added. Both are optional and each stage runs without them."""
    parts = []
    extracted = workspace.root / CONTEXT
    if extracted.exists():
        parts.append(extracted.read_text(encoding="utf-8"))
    notes = workspace.context_text()
    if notes:
        parts.append(notes)
    return "\n\n".join(parts)


def _evidence_record(workspace: Workspace):
    """The evidence record if ingestion has run, otherwise None."""
    path = workspace.root / EVIDENCE
    if not path.exists():
        return None
    try:
        return record_from_json(path)
    except (OSError, ValueError) as exc:
        logger.warning("Could not read the evidence record: %s", exc)
        return None


def _run_documents(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Read every submitted document, then answer each question from all of them at once.

    Only the groups that describe the agent are read (``evidence_files``) -- the owner's own
    scenarios are excluded, since reading them as evidence would let their blind spots into the
    benchmark by the back door, which is the thing an independent benchmark exists to avoid.
    """
    grouped = evidence_files(workspace.root)
    paths = [path for paths in grouped.values() for path in paths]
    if not paths:
        raise ValueError("Add at least one document before reading them.")

    # Which files carry the per-upload redaction toggle, resolved to actual paths here rather
    # than in ingestion: the workspace's notion of "group" is a webapp concept, and the ingestion
    # layer should only ever be told which files, not why.
    forced = {path for group, paths_in_group in grouped.items() for path in paths_in_group
             if workspace.is_marked_for_redaction(group, path.name)}

    result = ingest_documents([str(p) for p in paths], str(workspace.root / "ingest"),
                              progress=progress, cancel=cancel,
                              should_redact=lambda path: Path(path) in forced)
    workspace.state("documents").artifacts.update({
        "evidence": Path(result.evidence_path).name,
        "context": Path(result.context_path).name,
    })

    counts = result.summary
    unreadable = counts["documents"] - counts["readable"]
    return {"Documents read": counts["readable"],
            # Read and drawn on are different things, and the difference is the interesting one:
            # a document that contributed to nothing was either irrelevant or passed over.
            "Documents drawn on": f"{counts['drawn_on']} of {counts['readable']}",
            "Questions answered": f"{counts['answered']} of {len(FACETS)}",
            "Observations kept": counts["usable"],
            "Discarded as unsupported": counts["rejected"],
            "To put to the model owner": counts["to_ask"],
            "Unreadable files": unreadable}


def _run_intake(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Read the intake workbook and report the shape of what it declares.

    Where no workbook has been provided but the documents have been read, one is drafted from
    them first. Correcting a draft is an afternoon; writing one from a sixty-page document is a
    week, and the stage exists so a person does the correcting either way.
    """
    if not workspace.artifact_path("intake", "workbook"):
        context = workspace.root / CONTEXT
        if not context.exists():
            raise ValueError(
                "No intake workbook, and no documents have been read to draft one from. Either "
                "upload a completed intake here, or add documents on the first stage.")
        draft_intake_workbook(str(context), str(workspace.root / DRAFT_INTAKE),
                              evidence_path=str(workspace.root / EVIDENCE),
                              notes=workspace.note_lines())
        workspace.state("intake").artifacts["workbook"] = DRAFT_INTAKE
        logger.info("Drafted an intake from the context document.")

    intake = _intake(workspace)
    return {"Use case": intake.name, "Capabilities": len(intake.capabilities),
            "Decision points": len(intake.decisions), "States": len(intake.states),
            "Personas": len(intake.personas), "Tools": len(intake.tools)}


def _proposal_dicts(review) -> List[Dict[str, object]]:
    """A StructureReview's proposals as the plain dicts the workspace stores and the page reads.

    One flat shape either way, distinguished by ``kind``, rather than two differently-shaped rows
    -- the page renders both from one loop, and applying one is a single dispatch on this field.
    """
    rows: List[Dict[str, object]] = []
    for item in review.reconnections:
        rows.append({"kind": "reconnect", "target_kind": item.kind, "target_id": item.id,
                     "attach_to_state": item.attach_to_state, "reached_via": item.reached_via,
                     "rationale": item.rationale})
    for item in review.consolidations:
        rows.append({"kind": "consolidate", "decisions": item.decisions, "new_id": item.id,
                     "name": item.name, "outcomes": item.outcomes,
                     "outcome_map": item.outcome_map, "capabilities": item.capabilities,
                     "importance": item.importance, "rationale": item.rationale})
    return rows


def _apply_proposal(path: Path, entry: dict) -> bool:
    """Write one accepted proposal into the intake workbook. See core.intake for the mechanics."""
    if entry.get("kind") == "reconnect":
        if entry.get("target_kind") == "decision":
            return attach_decision_to_state(str(path), entry["attach_to_state"],
                                            entry["target_id"])
        return set_state_reached_via(str(path), entry["target_id"], entry["reached_via"])
    if entry.get("kind") == "consolidate":
        return merge_decisions(str(path), entry["decisions"], entry["new_id"], entry["name"],
                               entry["outcomes"], entry["outcome_map"],
                               primary_capability=(entry.get("capabilities") or [""])[0])
    return False


def _run_benchmark(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Enumerate every route through the declared graph and add the applicable probes."""
    intake = _intake(workspace)
    scenarios = build_scenarios(intake, with_probes=True)
    _save_registry(workspace, "benchmark", intake, scenarios)

    probes = sum(1 for s in scenarios if s.is_probe)
    return {"Scenarios": len(scenarios), "Routes through the graph": len(scenarios) - probes,
            "Probes": probes}


def _run_text(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Write each scenario up for the team that owns the agent."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    ScenarioWriter(context=_context(workspace), progress=progress,
                  cancel=cancel).write(scenarios, intake)
    _save_registry(workspace, "text", intake, scenarios)

    written = sum(1 for s in scenarios if s.description)
    return {"Scenarios written": written, "Turns scripted": sum(s.turn_count for s in scenarios)}


def _run_materiality(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Assign a tier to every scenario, with the redundancy signals in view."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    MaterialityAssessor(context=_context(workspace), progress=progress,
                        cancel=cancel).assess(scenarios, intake)
    _save_registry(workspace, "materiality", intake, scenarios)

    tiers = Counter(s.effective_materiality for s in scenarios)
    return {tier: tiers.get(tier, 0) for tier in MATERIALITY}


def _run_review(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """One pass over the whole benchmark, then rebuild the pack so its verdict actually lands."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    reviewer = ScenarioReviewer(context=_context(workspace), progress=progress, cancel=cancel)
    scenarios, proposals = reviewer.review(scenarios, intake)
    scenarios = list(scenarios) + list(proposals)

    _save_registry(workspace, "review", intake, scenarios)

    flagged = sum(1 for s in scenarios if getattr(s, "review_flag", ""))
    return {"Scenarios reviewed": len(scenarios) - len(proposals),
            "Proposed additions": len(proposals), "Flagged for a second look": flagged}


def _run_issue(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Write the challenge pack for the model owner and the registry kept internally."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    write_challenge_pack(str(workspace.root / PACK), intake, scenarios)
    _save_registry(workspace, "issue", intake, scenarios)
    workspace.state("issue").artifacts["challenge_pack"] = PACK

    runs = sum(required_runs(s.effective_materiality) for s in scenarios)
    return {"Scenarios issued": len(scenarios), "Runs requested": runs,
            "Expected outcomes in the pack": 0}


def _run_coverage(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Match the model owner's own scenario library against this benchmark.

    The file may have arrived on either stage -- with the rest of the pack, or here on its own --
    so both places are checked before the stage refuses to run.
    """
    owner = owner_scenario_file(workspace.root) or workspace.artifact_path(
        "coverage", "owner_scenarios")
    if not owner:
        raise ValueError(
            "No scenario library from the model owner. Upload one here, or add it on the documents "
            "stage under 'Their own test scenarios'. Skip this stage if they submitted none.")

    intake_path = workspace.artifact_path("intake", "workbook")
    if not intake_path:
        raise ValueError("No intake workbook yet. Provide one at the intake stage.")
    if not (workspace.root / REGISTRY).exists():
        raise ValueError("Build the benchmark first — there is nothing to match their scenarios "
                         "against.")

    try:
        result = map_coverage(str(intake_path), str(workspace.root / REGISTRY), str(owner),
                              str(workspace.root / OVERLAP), cancel=cancel)
    except UnreadableLibrary as exc:
        # Their file, not our pipeline. Say which file and what was wrong with it, because the
        # fix is to ask them for a clearer one rather than to change anything here.
        raise ValueError(
            f"'{Path(owner).name}' could not be read as a list of scenarios: {exc} Their file "
            f"needs one row or numbered line per scenario, with a description of at least a few "
            f"words. Send it back and ask for that, or attach a tidied copy here.") from exc

    workspace.state("coverage").artifacts["report"] = OVERLAP
    workspace.state("coverage").artifacts["registry"] = REGISTRY
    # map_coverage annotates the registry in place, so the snapshot is taken from the file rather
    # than written from scenarios this runner never loaded.
    shutil.copyfile(workspace.root / REGISTRY, workspace.root / _snapshot("coverage"))

    return {"Benchmark scenarios": result.benchmark,
            "Covered by their testing": result.covered,
            "Not covered": result.gaps,
            "Their scenarios read": result.owner_scenarios,
            "Read as": result.how_read}


# Every stage that does work has a runner. The two that only take input from the user -- the
# document pack and the intake workbook -- are handled by the upload route instead.
RUNNERS: Dict[str, Callable[..., Dict[str, object]]] = {
    "documents": _run_documents,
    "intake": _run_intake,
    "benchmark": _run_benchmark,
    "text": _run_text,
    "materiality": _run_materiality,
    "review": _run_review,
    "issue": _run_issue,
    "coverage": _run_coverage,
}
