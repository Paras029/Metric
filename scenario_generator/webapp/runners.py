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
from ..ingest import build_model_context, record_from_json
from ..ingest.groups import evidence_files, owner_scenario_file
from ..ingest.conversations import UnreadableConversations
from ..io import read_registry, read_scenarios, write_challenge_pack, write_registry
from ..llm import MaterialityAssessor, ScenarioReviewer, ScenarioWriter
from ..llm import cancellation
from ..pipeline import (build_scenarios, draft_intake_workbook, ingest_documents,
                        map_conversation_coverage, revise_intake_workbook, structural_problems)
from .coverageview import stored_report
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
    "intake": (EVIDENCE, CONTEXT, "ingest_questions.md", DRAFT_INTAKE),
    "workflow": (REGISTRY,),
    "coverage": (OVERLAP,),
    "summary": (PACK,),
}

# Each scenario stage also keeps a copy of the registry as it left it -- see _save_registry. They
# are listed against their own stage so that clearing one clears its snapshot with it, and a
# cleared stage stops showing a reading it is no longer claiming to have produced.
for _key in ("workflow", "scenarios", "variations", "materiality", "review",
             "coverage", "summary"):
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
    """Everything a later stage is grounded on: what the documents established, plus every note.

    Rendered from the evidence record rather than read off the context document, where the record
    is available. The two say the same things, but the document also carries the quote, source and
    page behind every claim -- provenance a person auditing it needs and a model cannot check --
    and that provenance is most of its length. Sending the compact rendering is what keeps the
    substance inside a budget that would otherwise have to start cutting it.

    Falls back to the document where the record cannot be read, which is what a workspace whose
    context file was supplied rather than extracted has.
    """
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
    """Read every submitted document, then answer each question from all of them at once.

    Only the groups that describe the agent are read (``evidence_files``) -- the model owner's own
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
    workspace.state("intake").artifacts.update({
        "evidence": Path(result.evidence_path).name,
        "context": Path(result.context_path).name,
    })

    counts = result.summary
    unreadable = counts["documents"] - counts["readable"]

    # Documents and images are counted apart because they are not the same submission. A workflow
    # drawn across five pictures is one flow sent as five files, and rolling it into "6 documents
    # read" says the pack was six times the size it was.
    summary: Dict[str, object] = {}
    if counts["texts"]:
        summary["Documents read"] = f"{counts['texts_read']} of {counts['texts']}"
    if counts["images"]:
        summary["Workflow images read"] = f"{counts['images_read']} of {counts['images']}"

    # Read and drawn on are different things, and the difference is the interesting one: a file
    # that contributed to nothing was either irrelevant or passed over.
    summary["Files anything rests on"] = f"{counts['drawn_on']} of {counts['readable']}"
    summary["Questions about the agent answered"] = f"{counts['answered']} of {len(FACETS)}"
    summary["Facts found and checked against the documents"] = counts["usable"]
    if counts["rejected"]:
        summary["Facts dropped — quote not found in any document"] = counts["rejected"]
    if counts["to_ask"]:
        summary["Left for you to answer at the intake stage"] = counts["to_ask"]
    if unreadable:
        summary["Files that could not be read at all"] = unreadable
    return summary


def _needs_reading(workspace: Workspace) -> bool:
    """Whether the submitted documents still have to be read before anything can be drafted.

    True where documents were submitted and either nothing has been read yet, or a file has been
    added or removed since -- the reading is of the pack as a whole, so a pack that has changed
    has not been read. Compared by name rather than by content: a file replaced under the same
    name is the case this cannot see, and re-reading a sixty-page pack on every run to catch it
    would cost far more than it saves. Running the stage again is always available.
    """
    submitted = {path.name for paths in evidence_files(workspace.root).values() for path in paths}
    if not submitted:
        return False
    record = _evidence_record(workspace)
    if record is None:
        return True
    return submitted != {document.name for document in record.documents}


def _run_intake(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Read whatever was submitted, then draft or revise the declaration from it.

    **Reading and drafting are one stage because they are one job.** The reading exists in order
    to be drafted from; nothing happens between them that a person decides. Splitting them put a
    Run button in the middle of a single thought, and made the questions the reading raised look
    like an artefact to work through rather than what they are -- notes against the rows of a
    draft. The reading is still exactly the same work, and everything it produces is still on
    disk; it simply is not a step anybody has to take on purpose.

    **A re-run revises rather than redrafts.** This is the whole difference between a second run
    that helps and one that undoes the first. Everything that has happened since the last run --
    an answer typed against a row, a note, a document added -- is new information about a
    declaration that already exists, and a fresh draft has no way to tell a correction somebody
    made by hand from something it should re-derive from nothing. So the current declaration goes
    to the model as *what to revise*, with instructions to carry forward everything the new
    information does not touch.

    A workbook you uploaded is never written to at all. A re-run reads it and reports on it,
    because a stage that silently replaced somebody's own file would be the worst thing this could
    do with a button labelled "run again".
    """
    report = progress or (lambda *args, **kwargs: None)
    provided = workspace.artifact_path("intake", "workbook")
    ours = provided is None or Path(provided).name == DRAFT_INTAKE
    context = workspace.root / CONTEXT
    target = workspace.root / DRAFT_INTAKE
    summary: Dict[str, object] = {}
    action = "read"

    # Reading first, and only where it is needed. An uploaded intake is authoritative, so a pack
    # submitted alongside it is still read -- later stages are grounded on that reading -- but it
    # never competes with the workbook for what the agent is.
    if _needs_reading(workspace):
        cancellation.check(cancel)
        summary.update(_read_documents(workspace, progress=report, cancel=cancel))

    if ours and provided is not None and target.exists():
        cancellation.check(cancel)
        action = "revised"
        revise_intake_workbook(str(target), str(target),
                               context_path=str(context) if context.exists() else None,
                               evidence_path=str(workspace.root / EVIDENCE),
                               notes=workspace.note_lines(), progress=report)
    elif ours:
        if not context.exists():
            raise ValueError(
                "Nothing to draft from. Add the model owner's documentation above, or upload a "
                "completed intake workbook if you already have one.")
        cancellation.check(cancel)
        action = "drafted"
        draft_intake_workbook(str(context), str(target),
                              evidence_path=str(workspace.root / EVIDENCE),
                              notes=workspace.note_lines(), progress=report)
        workspace.state("intake").artifacts["workbook"] = DRAFT_INTAKE
    else:
        report("Reading the intake workbook", 1, 2)

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
        "read": "read the workbook you provided; it is never overwritten",
    }[action]
    report("Intake ready", 2, 2)
    return summary


def _run_variations(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """The variation space. Not built yet, and honest about it.

    The stage exists so the pipeline has the shape it will keep, and so nothing downstream has to
    change when it is filled in: it reads the benchmark, writes it back unchanged, and takes its
    own snapshot exactly as every other scenario stage does. What it must not do is quietly report
    success as though it had produced something, which is why the result says what it says.
    """
    report = progress or (lambda *args, **kwargs: None)
    report("Reading the scenario space", 0, 2)
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    report("Passing it through unchanged", 1, 2)
    _save_registry(workspace, "variations", intake, scenarios)
    report("Nothing to vary yet", 2, 2)
    return {"Scenarios carried through": len(scenarios),
            "Variations written": "none — this stage is not built yet"}


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


def _run_workflow(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Enumerate every route through the declared graph and add the applicable probes."""
    report = progress or (lambda *args, **kwargs: None)
    report("Reading the intake", 0, 3)
    intake = _intake(workspace)
    report("Walking the graph", 1, 3)
    scenarios = build_scenarios(intake, with_probes=True)
    report("Writing the registry", 2, 3)
    _save_registry(workspace, "workflow", intake, scenarios)
    report("Built the benchmark", 3, 3)

    probes = sum(1 for s in scenarios if s.is_probe)
    return {"Scenarios": len(scenarios), "Routes through the graph": len(scenarios) - probes,
            "Probes": probes}


def _run_scenarios(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Write each scenario up for the model owner."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    ScenarioWriter(context=_context(workspace), progress=progress,
                  cancel=cancel).write(scenarios, intake)
    _save_registry(workspace, "scenarios", intake, scenarios)

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


def _run_summary(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Write the challenge pack for the model owner and the registry kept internally.

    Where the workspace is set to issue gaps only, the pack carries just the scenarios the model
    owner's own conversations under-cover. This is the one place in the pipeline that takes
    scenarios away rather than adding to them, and it is off unless someone turns it on: leaving a
    scenario out is a decision to accept the model owner's evidence for it, which is a judgement
    about how far that testing is trusted rather than anything this can work out.
    """
    report = progress or (lambda *args, **kwargs: None)
    report("Reading the benchmark", 0, 3)
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)

    # The registry keeps every scenario regardless -- narrowing what is *issued* must not narrow
    # what is on record, or the pack becomes the only surviving account of the benchmark.
    report("Writing the registry", 1, 3)
    _save_registry(workspace, "summary", intake, scenarios)

    issued, held_back = scenarios, 0
    gaps = _under_represented(workspace)
    if workspace.pack_gaps_only and gaps is not None:
        issued = [s for s in scenarios if s.id in gaps]
        held_back = len(scenarios) - len(issued)

    report("Writing the challenge pack", 2, 3)
    write_challenge_pack(str(workspace.root / PACK), intake, issued)
    workspace.state("summary").artifacts["challenge_pack"] = PACK
    report("Wrote the pack and the registry", 3, 3)

    runs = sum(required_runs(s.effective_materiality) for s in issued)
    summary = {"Scenarios issued": len(issued), "Runs requested": runs,
               "Expected outcomes in the pack": 0}
    if workspace.pack_gaps_only:
        summary["Held back as already covered"] = held_back
    return summary


def _under_represented(workspace: Workspace):
    """The ids coverage found under-represented, or None if coverage has not run.

    None and the empty set mean different things and the caller has to tell them apart: nothing
    mapped yet is not the same as everything covered, and filtering a pack down to nothing on the
    strength of a stage that never ran would be the worst outcome available.
    """
    if not workspace.coverage_mappings():
        return None
    report = stored_report(workspace, read_registry(str(workspace.root / REGISTRY)))
    if report is None:
        return None
    return {entry.scenario.id for entry in report.under_represented()}


def _run_coverage(workspace: Workspace, progress=None, cancel=None) -> Dict[str, object]:
    """Map the conversations the model owner actually ran onto this benchmark.

    The input is transcripts rather than a scenario list -- see :mod:`ingest.conversations` for
    why. The file may have arrived on either stage, with the rest of the pack or here on its own,
    so both places are checked before the stage refuses to run.
    """
    submitted = owner_scenario_file(workspace.root) or workspace.artifact_path(
        "coverage", "owner_scenarios")
    if not submitted:
        raise ValueError(
            "No conversations from the model owner. Upload the transcripts of what was run "
            "here, or add them on the documents stage under \"The model owner's own testing\". "
            "Skip this stage if none were submitted.")

    intake_path = workspace.artifact_path("intake", "workbook")
    if not intake_path:
        raise ValueError("No intake workbook yet. Provide one at the intake stage.")
    if not (workspace.root / REGISTRY).exists():
        raise ValueError("Build the benchmark first — there is nothing to map the "
                         "conversations against.")

    try:
        result = map_conversation_coverage(
            str(intake_path), str(workspace.root / REGISTRY), str(submitted),
            str(workspace.root / OVERLAP), threshold=workspace.coverage_threshold,
            progress=progress, cancel=cancel)
    except UnreadableConversations as exc:
        # A submitted file, not a pipeline fault. Say which file and what was wrong with it,
        # because the fix is to ask the model owner for a clearer one rather than to change
        # anything here.
        raise ValueError(
            f"'{Path(submitted).name}' could not be read as conversations: {exc} It needs the "
            f"turns of each exchange identifiable -- a conversation id with one row per turn, a "
            f"transcript per row, or 'User:'/'Agent:' prefixes in a document. Send it back and "
            f"ask for that, or attach a tidied copy here.") from exc

    # The pipeline has already written the counts back into the registry. Snapshot it the same way
    # every other scenario stage does, so this stage's page shows its own reading rather than
    # whatever a later stage has since made of the same file.
    shutil.copyfile(workspace.root / REGISTRY, workspace.root / _snapshot("coverage"))
    workspace.state("coverage").artifacts.update({"report": OVERLAP, "registry": REGISTRY})
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
