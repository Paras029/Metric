"""The local web interface: routes, and the wiring between a stage and the pipeline behind it.

This layer holds no pipeline logic of its own. Each stage delegates to the same functions the
command line calls, so the two front ends cannot drift apart, and a workspace part-finished here
can be finished there.

Run it with::

    python -m scenario_generator.webapp

It binds to localhost only. Nothing here is written for a shared deployment: there is no
authentication, and workspaces are readable by anyone who can reach the port.

Routes are declared with ``app.route(..., methods=[...])`` rather than the ``app.get`` and
``app.post`` shortcuts, and files are sent by string path rather than by ``Path``. Both are
Flask 2.0 conveniences, and the version installed here is whatever an internal mirror last
approved -- writing to the older interface costs nothing and removes a dependency on that.
"""
from __future__ import annotations

import logging
import re
import shutil
from collections import Counter
from pathlib import Path
import threading
from typing import Callable, Dict, List, Optional

from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file,
                   session, url_for)
from werkzeug.utils import secure_filename

from ..core.evidence import FACETS
from ..core.gaps import find_gaps
from ..core.generation import required_runs
from ..core.intake import (append_rows, attach_decision_to_state, merge_decisions, read_intake,
                           read_review_notes, set_decision_scope, set_state_reached_via,
                           write_template)
from ..core.models import MATERIALITY, IntakeData
from ..ingest import open_questions, read_owner_library, record_from_json
from ..ingest.context_document import FACET_HEADINGS
from ..ingest.groups import (ALL_EXTENSIONS, DEFAULT_GROUP, GROUP_BY_KEY, GROUPS, MODEL_DOC,
                             OWNER_SCENARIOS, SUPPORTING, evidence_files, folder_for, files_in,
                             owner_scenario_file, remove_file)
from ..ingest.owner_library import UnreadableLibrary
from ..io import read_scenarios, write_challenge_pack, write_registry
from ..llm import MaterialityAssessor, ScenarioReviewer, ScenarioWriter, config, metering
from ..llm.cancellation import Stopped
from ..llm.structure_review import review_structure
from ..pipeline import (build_scenarios, draft_intake_workbook, ingest_documents, map_coverage,
                        revise_intake_workbook)
from . import stagecancel
from .draft import DECISION, STATE, PendingEdits, PendingItem, next_id
from .graphview import graph_summary, render_svg
from .scenarios import FILTER_FIELDS, PAGE_SIZE, build_rows
from .stages import RUNNING, STAGE_BY_KEY, STAGES, STATUS_LABELS, downstream_of, index_of
from .workspace import Workspace, stage_view

# Stages whose output is a set of scenarios, so the page shows them rather than only counts.
SCENARIO_STAGES = ("text", "materiality", "review", "coverage", "issue")

# Groups redaction can actually do something to: the two that carry text ingestion reads. A
# diagram has no text to redact, and the owner's own scenario library never reaches build_corpus
# at all -- it is read separately, only to measure coverage, at a later stage.
REDACTABLE_GROUPS = (MODEL_DOC, SUPPORTING)

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path.cwd() / "workspaces"
UPLOAD_EXTENSIONS = set(ALL_EXTENSIONS) | {".xlsx", ".xlsm", ".csv"}
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
            "Minor points, recorded not asked": counts["set_aside"],
            "Unreadable files": unreadable}


def _run_intake(workspace: Workspace) -> Dict[str, object]:
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


def _run_benchmark(workspace: Workspace) -> Dict[str, object]:
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


def _run_issue(workspace: Workspace) -> Dict[str, object]:
    """Write the challenge pack for the model owner and the registry kept internally."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    write_challenge_pack(str(workspace.root / PACK), intake, scenarios)
    _save_registry(workspace, "issue", intake, scenarios)
    workspace.state("issue").artifacts["challenge_pack"] = PACK

    runs = sum(required_runs(s.effective_materiality) for s in scenarios)
    return {"Scenarios issued": len(scenarios), "Runs requested": runs,
            "Expected outcomes in the pack": 0}


def _run_coverage(workspace: Workspace) -> Dict[str, object]:
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
                              str(workspace.root / OVERLAP))
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


def create_app(workspace_root: Path = WORKSPACE_ROOT) -> Flask:
    app = Flask(__name__)
    app.secret_key = "scenario-generator-local"          # local single-user session only
    app.config["WORKSPACE_ROOT"] = Path(workspace_root)
    app.config["WORKSPACE_ROOT"].mkdir(parents=True, exist_ok=True)

    def _workspace() -> Workspace:
        slug = session.get("workspace")
        if not slug:
            abort(404)
        root = app.config["WORKSPACE_ROOT"] / slug
        if not (root / "workspace.json").exists():
            abort(404)
        return Workspace.load(root)

    @app.route("/")
    def index():
        return render_template("index.html",
                               workspaces=Workspace.list_all(app.config["WORKSPACE_ROOT"]))

    @app.route("/workspaces", methods=["POST"])
    def create_workspace():
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("index"))
        workspace = Workspace.create(app.config["WORKSPACE_ROOT"], name)
        session["workspace"] = workspace.root.name
        return redirect(url_for("stage", key=STAGES[0].key))

    @app.route("/workspaces/<slug>", methods=["GET"])
    def open_workspace(slug: str):
        session["workspace"] = slug
        workspace = _workspace()
        return redirect(url_for("stage", key=workspace.current_stage().key))

    @app.route("/stage/<key>", methods=["GET"])
    def stage(key: str):
        if key not in STAGE_BY_KEY:
            abort(404)
        workspace = _workspace()

        # Read once and pass it on. Both the graph and the benchmark panel need it, and reading a
        # workbook twice to render one page is a cost paid on every navigation.
        intake = None
        if workspace.artifact_path("intake", "workbook"):
            try:
                intake = _intake(workspace)
            except Exception as exc:                       # a malformed intake must not blank it
                logger.warning("Could not read the intake: %s", exc)

        # The graph is drawn wherever the intake is available, since it is the clearest reading
        # of what the benchmark will and will not be able to reach. On the intake stage it is
        # drawn over the sketch buffer, so an addition can be seen in place before it is committed.
        graph_svg, graph_facts, sketch = "", {}, None
        if key in ("intake", "benchmark") and intake is not None:
            try:
                pending = PendingEdits.from_list(workspace.pending)
                drawn = pending.merged(intake) if key == "intake" and pending else intake
                graph_svg = render_svg(drawn, pending.ids(DECISION), pending.ids(STATE))
                graph_facts = graph_summary(drawn, pending.ids(DECISION), pending.ids(STATE))
                if key == "intake":
                    sketch = {
                        "rows": pending.rows(intake), "count": len(pending),
                        "unattached": pending.unattached(intake),
                        # Only states the interaction can continue from. Nothing follows a state
                        # that ends the conversation, so offering one would let a person attach a
                        # decision nothing could ever reach.
                        "states": [s.id for s in intake.states if not s.is_terminal],
                        "decisions": [d.id for d in intake.decisions],
                        # Outcomes that no state already claims: a state is defined by the outcome
                        # that produces it, so an outcome already taken has nothing to attach.
                        "outcomes": _free_outcomes(intake),
                        "capabilities": [c.id for c in intake.capabilities]}
            except Exception as exc:                       # a malformed intake must not blank it
                logger.warning("Could not draw the graph: %s", exc)

        # Every already-declared decision, with whether it is walked -- shown only on the intake
        # stage, and only for what the workbook already has. A sketched decision is not here yet
        # to have a scope one way or the other.
        decisions = []
        if key == "intake" and intake is not None:
            decisions = [{"id": d.id, "name": d.name or d.id,
                         "outcomes": " / ".join(d.variants) or "none declared",
                         "out_of_scope": d.out_of_scope} for d in intake.decisions]

        return render_template(
            "stage.html",
            workspace=workspace,
            rail=[stage_view(workspace, s) for s in STAGES],
            view=stage_view(workspace, STAGE_BY_KEY[key]),
            runnable=key in RUNNERS,
            invalidated=request.args.get("invalidated", ""),
            notes=workspace.notes_for(key),
            note_total=len(workspace.notes),
            graph_svg=graph_svg,
            graph_facts=graph_facts,
            sketch=sketch,
            decisions=decisions,
            structure_proposals=workspace.structure_proposals if key == "intake" else [],
            structure_review_available=key == "intake" and intake is not None,
            intake_questions=_intake_questions_for(workspace, intake)
                if key == "intake" else None,
            answered=workspace.answered_questions(),
            answers=_answers_for(workspace) if key == "documents" else [],
            groups=_group_rows(workspace, key),
            benchmark=_benchmark_for(workspace, key, intake),
            materiality_tiers=MATERIALITY,
            produced={n: p for n, p in workspace.state(key).artifacts.items()
                      if not str(p).startswith("sources/")},
        )

    def _group_rows(workspace: Workspace, key: str):
        """What has been submitted under each heading, so gaps in the pack are visible.

        The coverage stage shows only the model owner's own scenarios: it is the one thing that
        stage consumes, and the rest of the pack is not its business.
        """
        if key == "documents":
            wanted = GROUPS
        elif key == "coverage":
            wanted = tuple(g for g in GROUPS if g.key == OWNER_SCENARIOS)
        else:
            return []

        rows = []
        for group in wanted:
            files = files_in(workspace.root, group.key)
            row = {"group": group, "files": [f.name for f in files], "note": "", "problem": False,
                  "redactable": group.key in REDACTABLE_GROUPS,
                  "redacted": {f.name for f in files
                              if workspace.is_marked_for_redaction(group.key, f.name)}}
            if group.key == OWNER_SCENARIOS and files:
                # Say how it was read now rather than when coverage runs, while there is still
                # time to ask them for a clearer file.
                try:
                    found, how = read_owner_library(files[0])
                    plural = "" if len(found) == 1 else "s"
                    row["note"] = f"{len(found)} scenario{plural} found — {how}"
                except Exception as exc:
                    row["note"] = f"This cannot be read as a scenario list: {exc}"
                    row["problem"] = True
            rows.append(row)
        return rows

    def _benchmark_for(workspace: Workspace, key: str, intake):
        """The scenarios as this stage left them, once there are any.

        A stage reads its own snapshot rather than the live registry, so coming back to the
        scenario-text page after materiality has run shows the text as it was written, not the
        text with tiers assigned afterwards beside it. Falling back to the live registry covers
        a workspace built before snapshots existed, and the stage that has not run yet.
        """
        if key not in SCENARIO_STAGES or intake is None:
            return None
        path = workspace.root / _snapshot(key)
        if not path.exists():
            path = workspace.root / REGISTRY
        if not path.exists():
            return None
        try:
            scenarios = read_scenarios(str(path), intake)
        except Exception as exc:                           # never blank the page over this
            logger.warning("Could not read the benchmark for display: %s", exc)
            return None
        filters = {field: request.args.get(f"filter_{field}", "") for field in FILTER_FIELDS}
        return build_rows(scenarios, request.args.get("view", "attention"), stage=key,
                          filters=filters, limit=_page_limit())

    def _page_limit() -> Optional[int]:
        """How many rows to render, from ``?limit=``: a number, ``all``, or the default."""
        raw = request.args.get("limit", "")
        if raw == "all":
            return None
        if raw.isdigit():
            return int(raw)
        return PAGE_SIZE

    def _answers_for(workspace: Workspace):
        """One row per question, so the reader can see coverage at a glance."""
        record = _evidence_record(workspace)
        if not record:
            return []
        rows = []
        for facet in FACETS:
            answer = record.answer_for(facet)
            rows.append({
                "heading": FACET_HEADINGS.get(facet, facet),
                "answered": bool(answer and answer.is_answered),
                "confidence": answer.confidence if answer else "Low",
                "points": len(answer.points) if answer else 0,
                "unknowns": len(answer.unknowns) if answer else 0,
                "answer": (answer.answer if answer else "") or "",
            })
        return rows

    # Which kind of row a gap addresses, in the order they are worth reading -- a decision that
    # names no outcome blocks enumeration entirely, so it comes before a persona's phrasing.
    _GAP_GROUP_ORDER = (("use_case", "Use case"), ("decision", "Decisions"), ("state", "States"),
                       ("capability", "Capabilities"), ("tool", "Tools"), ("persona", "Personas"))

    # A review note's own "field" text is free-form -- whatever the model wrote, not a schema
    # this can rely on -- so only an unambiguous row id is trusted to place it in a group. Ids
    # loose enough to false-match ordinary words (a persona's "P1" against any word starting with
    # a "p") are left to fall through to cross-cutting rather than risk a wrong placement.
    _ROW_ID_IN_TEXT = re.compile(r"\b(DEC-\d+|S-\d+|CAP-\d+)\b", re.I)
    _KIND_BY_PREFIX = {"DEC": "decision", "S": "state", "CAP": "capability"}

    def _intake_questions_for(workspace: Workspace, intake) -> Dict[str, list]:
        """What the current declaration needs, addressed to the row that needs it.

        Row-scoped gaps -- see core.gaps -- come first, grouped by the kind of row they concern,
        because that is how a person filling them in thinks about the intake: one decision, one
        state, one capability at a time. What is left is cross-cutting: a structural gap that is
        not about any single row (no state marked as the start), and whatever the documents never
        addressed at all, which is a property of the evidence rather than of the declaration and
        so cannot be pinned to one. Both use the same answer mechanism as everything else that
        adds context -- see :func:`save_answers` -- so answering one is recorded as a note under
        its own question, exactly like an open question always has been.
        """
        if intake is None:
            return {"row_groups": [], "cross_cutting": []}

        answered = workspace.answered_questions()

        def _row(question: str, why: str, heading: str = "") -> dict:
            return {"heading": heading, "question": question, "why": why,
                   "answered": question in answered}

        grouped: Dict[str, list] = {}
        cross_cutting = []
        for gap in find_gaps(intake):
            row = _row(gap.question, gap.why, gap.heading)
            if gap.kind:
                grouped.setdefault(gap.kind, []).append(row)
            else:
                cross_cutting.append(row)

        # The drafter's own hedges -- what it inferred rather than read, what it could not
        # settle -- read back from the workbook. A softer signal than a structural gap: the row
        # is filled in, but not with confidence, and only the model that wrote it knows why.
        path = workspace.artifact_path("intake", "workbook")
        if path:
            for note in read_review_notes(str(path)):
                field = str(note.get("field", ""))
                text = str(note.get("note", ""))
                if not text:
                    continue
                match = _ROW_ID_IN_TEXT.search(field) or _ROW_ID_IN_TEXT.search(text)
                question = f"About {field}: is this right?" if field else "Worth checking:"
                row = _row(question, text, match.group(1).upper() if match else field)
                kind = _KIND_BY_PREFIX.get(match.group(1).upper().split("-")[0]) if match else None
                if kind:
                    grouped.setdefault(kind, []).append(row)
                else:
                    cross_cutting.append(row)

        record = _evidence_record(workspace)
        if record is not None:
            for question in open_questions(record):
                cross_cutting.append(_row(question["question"], question["detail"],
                                          question["heading"]))

        row_groups = [{"label": label, "rows": grouped[key]}
                      for key, label in _GAP_GROUP_ORDER if grouped.get(key)]
        return {"row_groups": row_groups, "cross_cutting": cross_cutting}

    @app.route("/stage/<key>/note", methods=["POST"])
    def add_note(key: str):
        """Record something the user knows that the documents did not say.

        An answer to one of the open questions carries that question with it, so a later stage
        reads it as an answer rather than as a loose remark. Neither needs the stage re-run: the
        note joins the context every following stage receives.
        """
        workspace = _workspace()
        workspace.add_note(key, request.form.get("note", ""),
                           question=request.form.get("question", ""))
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/answers", methods=["POST"])
    def save_answers(key: str):
        """Save several answers at once, and accept a partial pass.

        The open questions are a list, and a list answered one item at a time is a page reload per
        item. Everything filled in is saved together; everything left blank is left open, so a
        person can settle what they know now and come back for the rest. What has been answered
        stays editable -- a second thought about an answer is worth more than the first one.
        """
        workspace = _workspace()
        entries = []
        for field in request.form:
            if not field.startswith("answer-"):
                continue
            question = request.form.get(f"question-{field[len('answer-'):]}", "")
            entries.append((question, request.form.get(field, "")))

        saved = workspace.add_notes(entries, key)
        logger.info("Recorded %d answer(s) at the %s stage.", saved, key)
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/upload", methods=["POST"])
    def upload(key: str):
        """Attach files to a stage.

        Everything the model owner sent lands in ``sources/<group>/``, whichever stage it was
        added from, so reading the pack later means reading a directory rather than guessing which
        of the workspace's files were source material. A file submitted without a stated kind goes
        to supporting material: the alternative was dropping it in the workspace root, where
        nothing ever looked for it again.

        Uploading is never the same as having produced a result. The stage stays ready and its
        runner is what completes it -- for the intake that means the workbook is read and its
        shape reported, and for coverage it means the match has actually run.
        """
        workspace = _workspace()
        uploads = [f for f in request.files.getlist("files") if f and f.filename]
        if not uploads:
            return redirect(url_for("stage", key=key))

        group = request.form.get("group", "") or (
            OWNER_SCENARIOS if key == "coverage" else DEFAULT_GROUP)
        if key == "intake":
            target = workspace.root
        else:
            group = group if group in GROUP_BY_KEY else DEFAULT_GROUP
            target = folder_for(workspace.root, group)
        target.mkdir(parents=True, exist_ok=True)

        stored, refused = [], []
        for upload_file in uploads:
            name = secure_filename(upload_file.filename)
            if Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
                refused.append(upload_file.filename)
                continue
            upload_file.save(str(target / name))
            stored.append(name)

        if not stored:
            workspace.mark_failed(key, _refusal(refused))
            return redirect(url_for("stage", key=key))

        if key == "intake":
            workspace.state("intake").artifacts["workbook"] = stored[0]
        else:
            for name in stored:
                workspace.state("documents").artifacts[name] = f"sources/{group}/{name}"

        # A new file invalidates the stage that reads it as well as everything built on top:
        # the reading itself has not seen this file, so its own reported result is out of date.
        invalidated = workspace.invalidate_from("documents" if key != "intake" else "intake")
        workspace.save()
        return redirect(url_for("stage", key=key,
                                invalidated=", ".join(s.title for s in invalidated)))

    @app.route("/stage/<key>/remove", methods=["POST"])
    def remove_upload(key: str):
        """Take a submitted file back out of the pack.

        A file uploaded to the wrong group, or superseded by a corrected copy, otherwise stays in
        the corpus for the rest of the workspace's life with no way to withdraw it.
        """
        workspace = _workspace()
        group, name = request.form.get("group", ""), request.form.get("name", "")
        if group in GROUP_BY_KEY and remove_file(workspace.root, group, name):
            workspace.state("documents").artifacts.pop(Path(name).name, None)
            workspace.set_redact(group, name, False)
            workspace.invalidate_from("documents")
            workspace.save()
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/redact", methods=["POST"])
    def toggle_redact(key: str):
        """Mark or unmark one uploaded file to be redacted ahead of the global setting.

        Takes effect the next time the documents stage runs -- this only records the choice.
        Changing it invalidates a completed documents stage the same way adding a file does: what
        the corpus was built from is different now, whether or not the file itself changed.
        """
        workspace = _workspace()
        group, name = request.form.get("group", ""), request.form.get("name", "")
        if group in REDACTABLE_GROUPS and name:
            workspace.set_redact(group, name, bool(request.form.get("on")))
            workspace.invalidate_from("documents")
            workspace.save()
        return redirect(url_for("stage", key=key))

    @app.route("/stage/intake/decision/<decision_id>/scope", methods=["POST"])
    def toggle_scope(decision_id: str):
        """Mark or unmark one already-declared decision as out of scope.

        A narrow exception to "edit the workbook and upload it again" -- see
        :func:`core.intake.set_decision_scope`. Writes straight to the workbook rather than the
        sketch buffer, because this changes something already declared rather than proposing
        something new, and it takes effect immediately the same way a corrected upload would.
        """
        workspace = _workspace()
        path = workspace.artifact_path("intake", "workbook")
        if not path:
            abort(404)
        if set_decision_scope(str(path), decision_id, bool(request.form.get("on"))):
            invalidated = workspace.invalidate_from("intake")
            workspace.save()
            return redirect(url_for("stage", key="intake",
                                    invalidated=", ".join(s.title for s in invalidated)))
        return redirect(url_for("stage", key="intake"))

    @app.route("/stage/intake/revise", methods=["POST"])
    def revise_intake():
        """Revise the current declaration with every note and answer given so far.

        Explicit rather than automatic -- nothing here runs the moment a question is answered,
        because a redraft has to be given the current declaration as what to revise or it would
        silently discard anything corrected by hand since the last one. See
        :func:`pipeline.revise_intake_workbook`. What is passed as ``notes`` is every note the
        workspace has ever recorded, old and new alike, the same accumulation every other pass
        already reads through :func:`_context`.
        """
        workspace = _workspace()
        path = workspace.artifact_path("intake", "workbook")
        if not path:
            abort(404)

        extracted = workspace.root / CONTEXT
        revise_intake_workbook(
            str(path), str(path),
            context_path=str(extracted) if extracted.exists() else None,
            evidence_path=str(workspace.root / EVIDENCE),
            notes=workspace.note_lines())

        invalidated = workspace.invalidate_from("intake")
        workspace.save()
        logger.info("Revised %s with %d note(s).", path.name, len(workspace.notes))
        return redirect(url_for("stage", key="intake",
                                invalidated=", ".join(s.title for s in invalidated)))

    @app.route("/stage/intake/structure-review", methods=["POST"])
    def run_structure_review():
        """One call: look for decisions and states worth reconnecting or consolidating.

        Synchronous rather than the background-thread machinery the pipeline stages use -- this is
        one call, not the dozens a document pack or a whole benchmark can take, so there is
        nothing here for a progress bar to usefully report on.
        """
        workspace = _workspace()
        path = workspace.artifact_path("intake", "workbook")
        if not path:
            abort(404)
        intake = _intake(workspace)
        review = review_structure(intake, context=_context(workspace))
        workspace.set_structure_proposals(_proposal_dicts(review))
        workspace.save()
        if not review:
            workspace.state("intake").note = (
                "Nothing to propose: every decision and state connects, and no decisions looked "
                "like alternate routes to the same fact.")
            workspace.save()
        return redirect(url_for("stage", key="intake", _anchor="structure-review"))

    @app.route("/stage/intake/structure-review/<proposal_id>/apply", methods=["POST"])
    def apply_structure_proposal(proposal_id: str):
        """Write one accepted proposal straight into the intake workbook.

        Unlike the sketch pad, there is no separate commit step: a structure review's proposal is
        a change to something already declared, not a new addition waiting on somewhere to attach,
        so accepting it behaves like the scope toggle -- immediate, and invalidating downstream
        the same way a corrected upload would.
        """
        workspace = _workspace()
        path = workspace.artifact_path("intake", "workbook")
        entry = workspace.pop_structure_proposal(proposal_id)
        if not path or entry is None:
            abort(404)

        applied = _apply_proposal(path, entry)
        invalidated = workspace.invalidate_from("intake") if applied else []
        workspace.state("intake").note = "" if applied else (
            "That proposal no longer matches the workbook -- something it referred to may have "
            "changed since it was made. It has been dropped rather than applied.")
        workspace.save()
        return redirect(url_for("stage", key="intake",
                                invalidated=", ".join(s.title for s in invalidated),
                                _anchor="structure-review"))

    @app.route("/stage/intake/structure-review/<proposal_id>/dismiss", methods=["POST"])
    def dismiss_structure_proposal(proposal_id: str):
        workspace = _workspace()
        workspace.pop_structure_proposal(proposal_id)
        workspace.save()
        return redirect(url_for("stage", key="intake", _anchor="structure-review"))

    # ----------------------------------------------------------------- sketching the intake
    #
    # Additions are held against the workspace and merged into the intake for display only, so the
    # graph can be tried out before the workbook is touched. The workbook stays the one authority
    # for what the benchmark is built from; this is a sketch pad in front of it.

    @app.route("/stage/intake/sketch", methods=["POST"])
    def sketch_intake():
        """Add a decision or a state to the sketch buffer."""
        workspace = _workspace()
        intake = _intake(workspace)
        pending = PendingEdits.from_list(workspace.pending)

        kind = request.form.get("kind", DECISION)
        if kind == STATE:
            identifier = next_id("S", [s.id for s in intake.states] + pending.ids(STATE))
            item = PendingItem(
                kind=STATE, id=identifier,
                name=(request.form.get("name") or "").strip(),
                reached_via=(request.form.get("reached_via") or "").strip(),
                next_decisions=[d.strip() for d in
                                request.form.getlist("next_decisions") if d.strip()],
                is_terminal=bool(request.form.get("is_terminal")),
                outcome_type=(request.form.get("outcome_type") or "").strip())
        else:
            identifier = next_id("DEC", [d.id for d in intake.decisions] + pending.ids(DECISION))
            item = PendingItem(
                kind=DECISION, id=identifier,
                name=(request.form.get("name") or "").strip(),
                capability=(request.form.get("capability") or "").strip(),
                outcomes=[o.strip() for o in
                          (request.form.get("outcomes") or "").split("/") if o.strip()],
                input_source=(request.form.get("input_source") or "User").strip())

        if not item.name:
            workspace.state("intake").note = "Give the new element a name before adding it."
            workspace.save()
            return redirect(url_for("stage", key="intake"))

        pending.add(item)
        workspace.pending = pending.to_list()
        workspace.save()
        return redirect(url_for("stage", key="intake", _anchor="sketch"))

    @app.route("/stage/intake/sketch/<item_id>/attach", methods=["POST"])
    def attach_sketch(item_id: str):
        """Give a sketched element somewhere to sit, so it moves into the flow."""
        workspace = _workspace()
        pending = PendingEdits.from_list(workspace.pending)
        item = next((i for i in pending.items if i.id == item_id), None)
        if item is None:
            abort(404)

        target = (request.form.get("target") or "").strip()
        if item.kind == STATE:
            # A state is defined by the outcome that produces it, so attaching one is naming that.
            item.reached_via = target
        else:
            # A decision is reached *from* a state, so attaching one means that state has to name
            # it as a next step. Where the state is itself a sketch the change lands on it now;
            # where it is already declared, it is recorded against the decision and the commit
            # amends the declared row.
            sketched = next((i for i in pending.items
                             if i.kind == STATE and i.id == target), None)
            if sketched is not None:
                if item.id not in sketched.next_decisions:
                    sketched.next_decisions.append(item.id)
            else:
                item.reached_from = target

        workspace.pending = pending.to_list()
        workspace.save()
        return redirect(url_for("stage", key="intake", _anchor="sketch"))

    @app.route("/stage/intake/sketch/<item_id>/remove", methods=["POST"])
    def remove_sketch(item_id: str):
        workspace = _workspace()
        pending = PendingEdits.from_list(workspace.pending)
        pending.remove(item_id)
        workspace.pending = pending.to_list()
        workspace.save()
        return redirect(url_for("stage", key="intake", _anchor="sketch"))

    @app.route("/stage/intake/sketch/commit", methods=["POST"])
    def commit_sketch():
        """Write the sketched additions into the intake workbook and empty the buffer.

        Rows are appended, so everything else in the workbook -- including anything edited there by
        hand -- survives. Once written, the benchmark and everything after it no longer reflect
        the intake, so they are marked out of date.
        """
        workspace = _workspace()
        path = workspace.artifact_path("intake", "workbook")
        if not path:
            raise ValueError("No intake workbook to write these into.")

        pending = PendingEdits.from_list(workspace.pending)
        decisions, states = pending.as_workbook_rows()
        added = append_rows(str(path), decisions=decisions, states=states,
                            links=pending.links())

        pending.clear()
        workspace.pending = pending.to_list()
        invalidated = workspace.invalidate_after("intake")
        workspace.save()
        logger.info("Wrote %d sketched row(s) into %s.", added, path.name)
        return redirect(url_for("stage", key="intake",
                                invalidated=", ".join(s.title for s in invalidated)))

    @app.route("/stage/<key>/scenario/<scenario_id>", methods=["POST"])
    def rule_on_scenario(key: str, scenario_id: str):
        """Record the reviewer's ruling on one scenario, straight into the registry.

        Two rulings, both already columns the registry carries. An override sets materiality and
        outranks every model pass, which is what makes the tiers a recommendation rather than a
        verdict. Clearing a flag says the review's concern has been considered and dismissed --
        the concern was never able to remove anything, so dismissing it removes only the marker.
        """
        workspace = _workspace()
        intake = _intake(workspace)
        scenarios = _scenarios(workspace, intake)
        scenario = next((s for s in scenarios if s.id == scenario_id), None)
        if scenario is None:
            abort(404)

        ruling = (request.form.get("materiality") or "").strip().title()
        if ruling in MATERIALITY:
            scenario.materiality_override = ruling
        elif ruling == "Clear":
            scenario.materiality_override = ""
        if request.form.get("clear_flag"):
            scenario.review_flag = ""

        write_registry(str(workspace.root / REGISTRY), intake, scenarios)

        # The pack's run counts come from materiality, so a pack written before this ruling no
        # longer reflects it. Saying so beats letting a stale workbook look current.
        workspace.invalidate_after("review")
        workspace.save()
        return redirect(url_for("stage", key=key, view=request.form.get("view", "attention"),
                                _anchor=scenario_id))

    @app.route("/stage/<key>/run", methods=["POST"])
    def run_stage(key: str):
        """Start a stage. Work happens on a background thread and the page polls for progress.

        Reading a sixty-page document is hundreds of model calls over several minutes. Running
        that inside the request would leave the browser on a blank tab with no way to tell a slow
        run from a dead one, which is the single worst thing this interface could do with the time
        it takes.
        """
        workspace = _workspace()
        if RUNNERS.get(key) is None:
            abort(404)
        if workspace.state(key).status == RUNNING:
            return redirect(url_for("stage", key=key))

        workspace.mark_running(key)
        root = workspace.root
        # Registered here, in the request that starts the run, rather than inside the thread it
        # starts -- so a stop clicked in the instant after this returns always finds a signal
        # waiting for it, rather than racing the new thread to create one.
        event = stagecancel.start(root, key)
        threading.Thread(target=_execute, args=(root, key, event), daemon=True).start()
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/stop", methods=["POST"])
    def stop_stage(key: str):
        """Ask a running stage to stop. Calls already sent finish; nothing further is sent.

        The stage is left exactly as it was before this run -- nothing partial is written -- so
        it comes back as ready to run again rather than as failed.
        """
        workspace = _workspace()
        stagecancel.stop(workspace.root, key)
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/progress", methods=["GET"])
    def stage_progress(key: str):
        """Where a running stage has got to, for the page to poll."""
        state = _workspace().state(key)
        return jsonify({"status": state.status, "percent": state.percent,
                        "message": state.progress.get("message", ""),
                        "done": state.progress.get("done", 0),
                        "total": state.progress.get("total", 0)})

    @app.route("/stage/<key>/reset", methods=["POST"])
    def reset_stage(key: str):
        """Clear a stage and everything after it, optionally deleting what they produced.

        Two behaviours because there are two intentions. Clearing the status alone keeps the old
        workbooks readable, which is what you want when comparing a rerun against what came
        before. Starting from scratch has to delete them, because several stages read what they
        need straight off disk and a status-only reset leaves the old work to come straight back.
        """
        workspace = _workspace()
        purge = bool(request.form.get("purge"))
        delete = []
        if purge:
            for stage in [STAGE_BY_KEY[key]] + downstream_of(key):
                delete.extend(STAGE_OUTPUTS.get(stage.key, ()))

        removed = workspace.reset_from(key, delete=delete)
        if purge:
            logger.info("Cleared %s onward and deleted %d file(s).", key, len(removed))
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/download/<name>", methods=["GET"])
    def download(key: str, name: str):
        workspace = _workspace()
        path = workspace.artifact_path(key, name)
        if not path:
            abort(404)
        return send_file(str(path), as_attachment=True)

    @app.route("/template", methods=["GET"])
    def blank_template():
        """A blank intake workbook, for a use case being described by hand."""
        workspace = _workspace()
        path = workspace.root / "intake_template.xlsx"
        write_template(str(path))
        return send_file(str(path), as_attachment=True)

    @app.template_filter("stage_number")
    def stage_number(key: str) -> int:
        return index_of(key) + 1

    @app.template_filter("stage_status")
    def stage_status(status: str) -> str:
        return STATUS_LABELS.get(status, status)

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    app = create_app()
    print("\n  Scenario generator — http://127.0.0.1:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()


def _execute(root: Path, key: str, cancel) -> None:
    """Run one stage on a background thread, reporting progress as it goes.

    The workspace is re-read here rather than handed across the thread boundary, so the record on
    disk stays the one source of truth for what has happened -- the same record the polling
    request reads, and the same one the command line would read.

    ``cancel`` is the stop signal ``run_stage`` registered before this thread was started. Only
    the stages that make many model calls take it -- see :func:`_takes_progress`, which the two
    always travel together with -- so a stop on any other stage is accepted without complaint but
    has nothing to interrupt beyond the one call already in flight.
    """
    workspace = Workspace.load(root)

    def report(message: str, done: int = 0, total: int = 0) -> None:
        workspace.report_progress(key, message, done, total)

    try:
        with metering.counted() as calls:
            summary = RUNNERS[key](workspace, progress=report, cancel=cancel) \
                if _takes_progress(key) else RUNNERS[key](workspace)
        if calls():
            summary = dict(summary, **{"Model calls": calls()})
            logger.info("Stage %s finished in %d model call(s).", key, calls())
    except Stopped:
        # Nothing this run would have written was: the pass raises before its caller reaches the
        # write. The workspace is exactly where it was before the run started.
        logger.info("Stage %s stopped by the user.", key)
        Workspace.load(root).mark_stopped(key)
        return
    except Exception as exc:                              # surfaced in the panel, not swallowed
        logger.exception("Stage %s failed", key)
        Workspace.load(root).mark_failed(key, str(exc))
        return
    finally:
        stagecancel.clear(root, key)

    finished = Workspace.load(root)
    finished.stages[key].artifacts.update(workspace.stages[key].artifacts)
    finished.complete(key, summary=summary)


def _takes_progress(key: str) -> bool:
    """Which stages report progress, and can be stopped mid-run: the ones that make many model
    calls.

    Reading documents was the only long stage while benchmarks were small. Writing, weighing and
    reviewing three hundred scenarios is dozens of batched calls each, and a stage that shows
    nothing for four minutes is indistinguishable from one that has died -- and is exactly the
    kind of run someone wants to be able to call off rather than sit through.
    """
    return key in ("documents", "text", "materiality", "review")


def _free_outcomes(intake: IntakeData) -> list:
    """Declared outcomes that no state is reached by yet.

    These are exactly the loose ends in the declaration: a branch the documents named but whose
    destination nobody wrote down. Offering them as the place to attach a new state turns the
    completeness report into something a person can act on in one click.
    """
    taken = {s.reached_via.strip().lower().replace(" ", "") for s in intake.states}
    return [f"{d.id}={v}" for d in intake.decisions for v in d.variants
            if f"{d.id}={v}".lower().replace(" ", "") not in taken]


def _refusal(names) -> str:
    """Why nothing was stored, named precisely enough to act on."""
    if not names:
        return "No file was uploaded."
    supported = ", ".join(sorted(UPLOAD_EXTENSIONS))
    return (f"Nothing was stored. {', '.join(names)} — this reads {supported}. A .doc or .xls from "
            f"an older Office version needs saving as .docx or .xlsx first.")
