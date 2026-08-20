"""What each stage of the pipeline actually does, and where it puts the result."""
from __future__ import annotations

import logging
import shutil
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List

from metric.domain.models import required_variations
from metric.domain.intake import attach_decision_to_state, merge_decisions, read_intake, set_state_reached_via
from metric.domain.models import MATERIALITY, IntakeData
from metric.phases.intake.intake import build_model_context, record_from_json
from metric.phases.intake.intake.groups import OWNER_SCENARIOS, evidence_files, owner_scenario_file
from metric.phases.coverage.coverage.conversations import UnreadableConversations
from metric.domain import read_space_metadata, read_scenarios, write_data_template, write_space_metadata
from metric.phases.scenario_generator.materiality.assess import MaterialityAssessor
from metric.phases.scenario_generator.review.reviewer import ScenarioReviewer
from metric.phases.scenario_generator.scenarios.writer import ScenarioWriter
from metric.llm.gateway import ask_llm
from metric.llm import cancellation, config
from metric.domain.graph import Limits
from metric.pipeline import build_scenario_space, draft_intake_workbook, ingest_documents, map_conversation_coverage, revise_intake_workbook, structural_problems
from metric.phases.coverage.coverage.view import stored_report
from metric.web.workspace import Workspace

logger = logging.getLogger(__name__)

DRAFT_INTAKE = "drafted_intake.xlsx"


# --------------------------------------------------------------------------- stage runners
#
# Each runner takes the workspace, does one stage's work, and returns the figures worth showing.
# None of them contain pipeline logic: they read what the previous stage left on disk, call the
# same functions the command line calls, and write their own output back. Anything they raise is
# shown in the stage panel rather than swallowed.

METADATA = "scenario_space_metadata.xlsx"
EVIDENCE = "ingest_evidence.json"
CONTEXT = "ingest_context.md"
TEMPLATE = "data_template.xlsx"
OVERLAP = "coverage.xlsx"

# What each stage puts on disk, so clearing a stage can actually clear it. Listed against the
# stage that *creates* the file rather than every stage that rewrites it: clearing the scenario
# text does not mean throwing away the scenario space metadata the workflow stage built.
#
# Submitted documents appear nowhere here. They are input rather than output, and each already has
# its own remove -- deleting the data template because a later stage was re-run would be a rout.
STAGE_OUTPUTS: Dict[str, tuple] = {
    "intake": (EVIDENCE, CONTEXT, "ingest_questions.md", DRAFT_INTAKE),
    "workflow": (METADATA,),
    "coverage": (OVERLAP,),
    "summary": (TEMPLATE,),
}

# Each scenario stage also keeps a copy of the scenario space metadata as it left it -- see _save_metadata. They
# are listed against their own stage so that clearing one clears its snapshot with it, and a
# cleared stage stops showing a reading it is no longer claiming to have produced.
for _key in ("workflow", "scenarios", "variations", "materiality", "review",
             "coverage", "summary"):
    STAGE_OUTPUTS[_key] = STAGE_OUTPUTS.get(_key, ()) + (f"space_metadata.{_key}.xlsx",)


def _snapshot(key: str) -> str:
    """What a stage's own copy of the scenario space metadata is called."""
    return f"space_metadata.{key}.xlsx"


def _save_metadata(workspace: Workspace, key: str, intake: IntakeData, scenarios) -> None:
    """Write the live metadata workbook, and keep a copy of it as this stage left it."""
    write_space_metadata(str(workspace.root / METADATA), intake, scenarios)
    shutil.copyfile(workspace.root / METADATA, workspace.root / _snapshot(key))
    workspace.state(key).artifacts["space_metadata"] = METADATA


def _intake(workspace: Workspace) -> IntakeData:
    path = workspace.artifact_path("intake", "workbook")
    if not path:
        raise ValueError("No intake workbook yet. Provide one at the intake stage.")
    return read_intake(str(path))


def _scenarios(workspace: Workspace, intake: IntakeData):
    """The scenario space as the last stage left it."""
    path = workspace.root / METADATA
    if not path.exists():
        raise ValueError("Build the scenario space first.")
    return read_scenarios(str(path), intake)


def _context(workspace: Workspace) -> str:
    """Everything a later stage is grounded on: what the documents established, plus every note."""
    parts = []
    record = _evidence_record(workspace)
    if record is not None:
        parts.append(build_model_context(record))
    else:
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


def _read_documents(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Read every submitted document, then answer each question from all of them at once."""
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
                              should_redact=lambda path: Path(path) in forced,
                              diagram_mode=workspace.diagram_mode)
    workspace.state("intake").artifacts.update({
        "evidence": Path(result.evidence_path).name,
        "context": Path(result.context_path).name,
    })

    counts = result.summary
    unreadable = counts["documents"] - counts["readable"]

    # What a person can act on, and nothing else. How many facts were extracted, how many were
    # dropped for want of a quote and how many of eleven internal questions came back answered are
    # all real numbers, and all of them are about the machinery rather than about the agent being
    # validated -- they belong in the log and in the evidence file, both of which still have them.
    summary: Dict[str, object] = {}
    if counts["texts"]:
        summary["Documents read"] = f"{counts['texts_read']} of {counts['texts']}"
    if counts["images"]:
        summary["Workflow images read"] = f"{counts['images_read']} of {counts['images']}"
    if counts["readable"] and counts["drawn_on"] < counts["readable"]:
        summary["Documents nothing rests on"] = counts["readable"] - counts["drawn_on"]
    if unreadable:
        summary["Files that could not be read at all"] = unreadable
    return summary


def _needs_reading(workspace: Workspace) -> bool:
    """Whether the submitted documents still have to be read before anything can be drafted."""
    submitted = {path.name for paths in evidence_files(workspace.root).values() for path in paths}
    if not submitted:
        return False
    record = _evidence_record(workspace)
    if record is None:
        return True
    return submitted != {document.name for document in record.documents}


def _run_intake(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Read whatever was submitted, then draft or revise the declaration from it."""
    report = progress or (lambda *args, **kwargs: None)
    provided = workspace.artifact_path("intake", "workbook")
    ours = provided is None or Path(provided).name == DRAFT_INTAKE
    context = workspace.root / CONTEXT
    target = workspace.root / DRAFT_INTAKE
    summary: Dict[str, object] = {}
    action = "read"

    # One path or the other, never both. The loop replaces the reading and drafting sequence
    # rather than following it: running it afterwards spent seven to nine calls on the sequence
    # and then up to twenty-four more going over the same documents, which is the same work
    # bought twice.
    if ours and config.intake_loop():
        cancellation.check(cancel)
        summary.update(_read_and_draft_with_loop(workspace, target, report))
        workspace.state("intake").artifacts["workbook"] = DRAFT_INTAKE
        action = "looped"
    else:
        # Reading first, and only where it is needed. An uploaded intake is authoritative, so a
        # pack submitted alongside it is still read -- later stages are grounded on that reading
        # -- but it never competes with the workbook for what the agent is.
        if _needs_reading(workspace):
            cancellation.check(cancel)
            summary.update(_read_documents(workspace, progress=report, cancel=cancel))
        _draft_or_revise(workspace, provided, target, context, ours, report, cancel, summary)
        action = summary.pop("_action", "read")

    intake = _intake(workspace)
    summary.update({
        "Use case": intake.name, "Capabilities": len(intake.capabilities),
        "Decision points": len(intake.decisions), "States": len(intake.states),
        "Personas": len(intake.personas), "Tools": len(intake.tools)})

    # What is still structurally missing. A count here is what tells you whether the run improved
    # the declaration; the notes on the rows themselves tell you what to do about it.
    outstanding = structural_problems(intake)
    summary["Still needs an answer"] = len(outstanding) or "nothing — the graph is complete"
    summary["This run"] = {
        "drafted": "drafted the declaration from the documentation",
        "revised": ("revised the declaration already here, folding in every answer and note "
                    "since — nothing they do not touch was changed"),
        "read": "read the workbook provided; it is never overwritten",
        "looped": ("read the pack and filled the declaration in a loop, stopping when it audited "
                   "clean rather than when the calls ran out"),
    }[action]
    report("Intake ready", 2, 2)
    return summary


def _draft_or_revise(workspace: Workspace, provided, target: Path, context: Path, ours: bool,
                     report, cancel, summary: Dict[str, object]) -> None:
    """The fixed sequence: draft a declaration, or revise the one already here."""
    if ours and provided is not None and target.exists():
        cancellation.check(cancel)
        summary["_action"] = "revised"
        revise_intake_workbook(str(target), str(target),
                               context_path=str(context) if context.exists() else None,
                               evidence_path=str(workspace.root / EVIDENCE),
                               notes=workspace.note_lines(), progress=report)
    elif ours:
        if not context.exists():
            raise ValueError(
                "Nothing to draft from yet. Add the model owner's documentation, a workflow "
                "diagram, or any combination of the two above — a pack of pictures is a pack. Or "
                "upload a completed intake workbook instead.")
        cancellation.check(cancel)
        summary["_action"] = "drafted"
        draft_intake_workbook(str(context), str(target),
                              evidence_path=str(workspace.root / EVIDENCE),
                              notes=workspace.note_lines(), progress=report)
        workspace.state("intake").artifacts["workbook"] = DRAFT_INTAKE
    else:
        summary["_action"] = "read"
        report("Reading the intake workbook", 1, 2)


def _read_and_draft_with_loop(workspace: Workspace, target: Path, report) -> Dict[str, object]:
    """Read the pack and fill the declaration in one loop, and report what it managed."""
    from metric.phases.intake.intake import agent

    try:
        state = agent.run(workspace.root, str(target), progress=report,
                          should_redact=lambda path: workspace.is_marked_for_redaction(
                              _group_of(workspace, path), Path(path).name),
                          notes=workspace.note_lines(),
                          # How several submitted images relate to one another. Guessed at, it
                          # welds two drawings of one flow end to end and invents routes the agent
                          # does not have -- and the person who uploaded them already said.
                          diagram_mode=workspace.diagram_mode)
    except Exception as exc:
        logger.warning("The intake loop could not run (%s); falling back to the fixed sequence.",
                       exc)
        context = workspace.root / CONTEXT
        summary: Dict[str, object] = {"The loop": f"could not run ({exc}); read the pack the "
                                                  f"ordinary way instead"}
        if _needs_reading(workspace):
            summary.update(_read_documents(workspace, progress=report))
        _draft_or_revise(workspace, workspace.artifact_path("intake", "workbook"), target,
                         context, True, report, None, summary)
        summary.pop("_action", None)
        return summary

    # The questions are not filed anywhere separately, and that is deliberate. Every one of them
    # has to name a row the audit is already complaining about -- that is the filter it passed to
    # be asked at all -- so each is already on this page as an open question against that row.
    # Writing them a second time would put the same question in two places and make answering it
    # in one of them look like leaving it open in the other.
    return {"The loop": state.summary()}


def _group_of(workspace: Workspace, path) -> str:
    """Which upload group a submitted file came from, for the per-file redaction toggle."""
    for group, paths in evidence_files(workspace.root).items():
        if any(p == Path(path) for p in paths):
            return group
    return "supporting"


def _run_variations(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """The variation space. Not built yet, and honest about it."""
    report = progress or (lambda *args, **kwargs: None)
    report("Reading the scenario space", 0, 2)
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    report("Passing it through unchanged", 1, 2)
    _save_metadata(workspace, "variations", intake, scenarios)
    report("Nothing to vary yet", 2, 2)
    return {"Scenarios carried through": len(scenarios),
            "Variations written": "none — this stage is not built yet"}


def _proposal_dicts(review) -> List[Dict[str, object]]:
    """A StructureReview's proposals as the plain dicts the workspace stores and the page reads."""
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


def _run_workflow(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Enumerate every route through the declared graph and add the applicable probes."""
    report = progress or (lambda *args, **kwargs: None)
    report("Reading the intake", 0, 3)
    intake = _intake(workspace)
    report("Walking the graph", 1, 3)
    limits = Limits()
    scenarios = build_scenario_space(intake, with_probes=True, limits=limits)
    report("Writing the scenario space metadata", 2, 3)
    _save_metadata(workspace, "workflow", intake, scenarios)
    report("Built the scenario space", 3, 3)

    probes = sum(1 for s in scenarios if s.is_probe)
    facts = {"Scenarios": len(scenarios), "Routes through the graph": len(scenarios) - probes,
             "Probes": probes}
    # Said on the stage rather than left in a log. An incomplete scenario space that does not
    # know it is incomplete is the one failure this whole stage cannot be checked for by reading
    # its output: what is missing is missing.
    if limits.truncated:
        facts["Enumeration"] = ("Stopped at a backstop — the set is incomplete"
                                if limits.paths else
                                "Some branches were cut for length — the set is incomplete")
    return facts


def _run_scenarios(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Write each scenario up for the model owner."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    ScenarioWriter(context=_context(workspace), progress=progress,
                  cancel=cancel).write(scenarios, intake)
    _save_metadata(workspace, "scenarios", intake, scenarios)

    written = sum(1 for s in scenarios if s.description)
    return {"Scenarios written": written, "Turns scripted": sum(s.turn_count for s in scenarios)}


def _run_materiality(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Assign a tier to every scenario, with the redundancy signals in view."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    MaterialityAssessor(context=_context(workspace), progress=progress,
                        cancel=cancel).assess(scenarios, intake)
    _save_metadata(workspace, "materiality", intake, scenarios)

    tiers = Counter(s.effective_materiality for s in scenarios)
    return {tier: tiers.get(tier, 0) for tier in MATERIALITY}


def _run_review(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """One pass over the whole scenario space, then rebuild the data template so its verdict actually lands."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    reviewer = ScenarioReviewer(context=_context(workspace), progress=progress, cancel=cancel)
    scenarios, proposals = reviewer.review(scenarios, intake)
    scenarios = list(scenarios) + list(proposals)

    _save_metadata(workspace, "review", intake, scenarios)

    flagged = sum(1 for s in scenarios if getattr(s, "review_flag", ""))
    return {"Scenarios reviewed": len(scenarios) - len(proposals),
            "Proposed additions": len(proposals), "Flagged for a second look": flagged}


def _run_summary(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Write the data template for the model owner and the scenario space metadata kept internally."""
    report = progress or (lambda *args, **kwargs: None)
    report("Reading the scenario space", 0, 3)
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)

    # The scenario space metadata keeps every scenario regardless -- narrowing what is *issued* must not narrow
    # what is on record, or the pack becomes the only surviving account of the scenario space.
    report("Writing the scenario space metadata", 1, 3)
    _save_metadata(workspace, "summary", intake, scenarios)

    issued, held_back = scenarios, 0
    gaps = _under_represented(workspace)
    if workspace.pack_gaps_only and gaps is not None:
        issued = [s for s in scenarios if s.id in gaps]
        held_back = len(scenarios) - len(issued)

    report("Writing the data template", 2, 3)
    write_data_template(str(workspace.root / TEMPLATE), intake, issued)
    workspace.state("summary").artifacts["challenge_pack"] = TEMPLATE
    report("Wrote the pack and the scenario space metadata", 3, 3)

    runs = sum(required_variations(s.effective_materiality) for s in issued)
    summary = {"Scenarios issued": len(issued), "Variations requested": runs,
               "Expected outcomes in the pack": 0}
    if workspace.pack_gaps_only:
        summary["Held back as already covered"] = held_back
    return summary


def _under_represented(workspace: Workspace):
    """The ids coverage found under-represented, or None if coverage has not run."""
    if not workspace.coverage_mappings():
        return None
    report = stored_report(workspace, read_space_metadata(str(workspace.root / METADATA)))
    if report is None:
        return None
    return {entry.scenario.id for entry in report.under_represented()}


def _run_coverage(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Map the conversations the model owner actually ran onto this scenario space."""
    submitted = owner_scenario_file(workspace.root) or workspace.artifact_path(
        "coverage", "owner_scenarios")
    if not submitted:
        raise ValueError(
            "No conversations from the model owner. Upload the transcripts of what was run "
            "here, or add them on the documents stage under \"The model owner's testing\". "
            "Skip this stage if none were submitted.")

    intake_path = workspace.artifact_path("intake", "workbook")
    if not intake_path:
        raise ValueError("No intake workbook yet. Provide one at the intake stage.")
    if not (workspace.root / METADATA).exists():
        raise ValueError("Build the scenario space first — there is nothing to map the "
                         "conversations against.")

    try:
        result = map_conversation_coverage(
            str(intake_path), str(workspace.root / METADATA), str(submitted),
            str(workspace.root / OVERLAP), threshold=workspace.coverage_threshold,
            redact=workspace.is_marked_for_redaction(OWNER_SCENARIOS, Path(submitted).name),
            complete=ask_llm, progress=progress, cancel=cancel)
    except UnreadableConversations as exc:
        # A submitted file, not a pipeline fault. Say which file and what was wrong with it,
        # because the fix is to ask the model owner for a clearer one rather than to change
        # anything here.
        raise ValueError(
            f"'{Path(submitted).name}' could not be read as conversations: {exc} It needs the "
            f"turns of each exchange identifiable -- a conversation id with one row per turn, a "
            f"transcript per row, or 'User:'/'Agent:' prefixes in a document. Send it back and "
            f"ask for that, or attach a tidied copy here.") from exc

    # The pipeline has already written the counts back into the scenario space metadata. Snapshot it the same way
    # every other scenario stage does, so this stage's page shows its own reading rather than
    # whatever a later stage has since made of the same file.
    shutil.copyfile(workspace.root / METADATA, workspace.root / _snapshot("coverage"))
    workspace.state("coverage").artifacts.update({"report": OVERLAP, "space_metadata": METADATA})
    workspace.save_coverage(result.mappings, result.how_read)

    counts = result.summary
    return {**counts, "Read as": result.how_read,
            "Represented at": f"{workspace.coverage_threshold}+ conversations"}


# Every stage that does work has a runner. The two that only take input from the user -- the
# document pack and the intake workbook -- are handled by the upload route instead.
RUNNERS: Dict[str, Callable[..., Dict[str, object]]] = {
    "intake": _run_intake,
    "workflow": _run_workflow,
    "scenarios": _run_scenarios,
    "variations": _run_variations,
    "materiality": _run_materiality,
    "review": _run_review,
    "coverage": _run_coverage,
    "summary": _run_summary,
}
