"""The local web interface: routes, and the wiring between a stage and the pipeline behind it.

This layer holds no pipeline logic of its own. Each stage delegates to the same functions the
command line calls, so the two front ends cannot drift apart, and a workspace part-finished here
can be finished there.

Run it with::

    python -m scenario_generator.webapp

It binds to localhost only. Nothing here is written for a shared deployment: there is no
authentication, and workspaces are readable by anyone who can reach the port.
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Callable, Dict

from flask import (Flask, abort, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.utils import secure_filename

from ..core.evidence import FACETS
from ..core.generation import required_runs
from ..core.intake import read_intake, write_template
from ..core.models import MATERIALITY, IntakeData
from ..ingest import open_questions, record_from_json
from ..ingest.context_document import FACET_HEADINGS
from ..io import read_scenarios, write_challenge_pack, write_registry
from ..llm import MaterialityAssessor, ScenarioReviewer, ScenarioWriter
from ..pipeline import build_scenarios, ingest_documents, map_coverage, render_questions
from .graphview import graph_summary, render_svg
from .stages import STAGE_BY_KEY, STAGES, STATUS_LABELS, index_of
from .workspace import Workspace, stage_view

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path.cwd() / "workspaces"
UPLOAD_EXTENSIONS = {".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg", ".md", ".txt", ".xlsx"}


# --------------------------------------------------------------------------- stage runners
#
# Each runner takes the workspace, does one stage's work, and returns the figures worth showing.
# None of them contain pipeline logic: they read what the previous stage left on disk, call the
# same functions the command line calls, and write their own output back. Anything they raise is
# shown in the stage panel rather than swallowed.

REGISTRY = "registry.xlsx"
EVIDENCE = "ingest_evidence.json"
CONTEXT = "ingest_context.md"
QUESTIONS = "open_questions.md"
PACK = "challenge_pack.xlsx"
OVERLAP = "coverage.xlsx"


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


def _source_files(workspace: Workspace):
    folder = workspace.root / "sources"
    return sorted(p for p in folder.glob("*") if p.is_file()) if folder.exists() else []


def _run_evidence(workspace: Workspace) -> Dict[str, object]:
    """Read the submitted documents into a verified evidence record, and write the context file."""
    paths = _source_files(workspace)
    if not paths:
        raise ValueError("No documents have been added at the submitted documents stage.")

    result = ingest_documents([str(p) for p in paths], str(workspace.root / "ingest"))
    workspace.state("evidence").artifacts.update({
        "evidence": Path(result.evidence_path).name,
        "context": Path(result.context_path).name,
    })

    counts = result.summary
    unreadable = sum(1 for d in result.record.documents if d.kind == "unreadable")
    return {"Documents read": counts["documents"] - unreadable,
            "Questions answered": f"{counts['answered']} of {len(FACETS)}",
            "Observations kept": counts["usable"],
            "Discarded as unsupported": counts["rejected"],
            "Points left unsettled": counts["unknowns"],
            "Questions unanswered": counts["empty_facets"],
            "Unreadable files": unreadable}


def _run_questions(workspace: Workspace) -> Dict[str, object]:
    """Turn the evidence record into the list of things nobody's documents answered."""
    record = _evidence_record(workspace)
    if record is None:
        raise ValueError("Extract the evidence first.")

    questions = open_questions(record)
    path = workspace.root / QUESTIONS
    path.write_text(render_questions(questions), encoding="utf-8")
    workspace.state("questions").artifacts["questions"] = QUESTIONS

    gaps = sum(1 for q in questions if q["kind"] == "gap")
    return {"Open questions": len(questions), "Categories not covered": gaps,
            "Statements to confirm": len(questions) - gaps,
            "Notes you have added": len(workspace.notes)}


def _run_intake(workspace: Workspace) -> Dict[str, object]:
    """Read the intake workbook and report the shape of what it declares."""
    intake = _intake(workspace)
    return {"Use case": intake.name, "Capabilities": len(intake.capabilities),
            "Decision points": len(intake.decisions), "States": len(intake.states),
            "Personas": len(intake.personas), "Tools": len(intake.tools)}


def _run_benchmark(workspace: Workspace) -> Dict[str, object]:
    """Enumerate every route through the declared graph and add the applicable probes."""
    intake = _intake(workspace)
    scenarios = build_scenarios(intake, with_probes=True)
    write_registry(str(workspace.root / REGISTRY), intake, scenarios)
    workspace.state("benchmark").artifacts["registry"] = REGISTRY

    probes = sum(1 for s in scenarios if s.is_probe)
    return {"Scenarios": len(scenarios), "Routes through the graph": len(scenarios) - probes,
            "Probes": probes}


def _run_text(workspace: Workspace) -> Dict[str, object]:
    """Write each scenario up for the team that owns the agent."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    ScenarioWriter(context=_context(workspace)).write(scenarios, intake)
    write_registry(str(workspace.root / REGISTRY), intake, scenarios)
    workspace.state("text").artifacts["registry"] = REGISTRY

    written = sum(1 for s in scenarios if s.description)
    return {"Scenarios written": written, "Turns scripted": sum(s.turn_count for s in scenarios)}


def _run_materiality(workspace: Workspace) -> Dict[str, object]:
    """Assign a tier to every scenario, with the redundancy signals in view."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    MaterialityAssessor(context=_context(workspace)).assess(scenarios, intake)
    write_registry(str(workspace.root / REGISTRY), intake, scenarios)
    workspace.state("materiality").artifacts["registry"] = REGISTRY

    tiers = Counter(s.effective_materiality for s in scenarios)
    return {tier: tiers.get(tier, 0) for tier in MATERIALITY}


def _run_review(workspace: Workspace) -> Dict[str, object]:
    """One pass over the whole benchmark, then rebuild the pack so its verdict actually lands."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    reviewer = ScenarioReviewer(context=_context(workspace))
    scenarios, proposals = reviewer.review(scenarios, intake)
    scenarios = list(scenarios) + list(proposals)

    write_registry(str(workspace.root / REGISTRY), intake, scenarios)
    workspace.state("review").artifacts["registry"] = REGISTRY

    flagged = sum(1 for s in scenarios if getattr(s, "review_flag", ""))
    return {"Scenarios reviewed": len(scenarios) - len(proposals),
            "Proposed additions": len(proposals), "Flagged for a second look": flagged}


def _run_issue(workspace: Workspace) -> Dict[str, object]:
    """Write the challenge pack for the model owner and the registry kept internally."""
    intake = _intake(workspace)
    scenarios = _scenarios(workspace, intake)
    write_challenge_pack(str(workspace.root / PACK), intake, scenarios)
    write_registry(str(workspace.root / REGISTRY), intake, scenarios)
    workspace.state("issue").artifacts.update({"challenge_pack": PACK, "registry": REGISTRY})

    runs = sum(required_runs(s.effective_materiality) for s in scenarios)
    return {"Scenarios issued": len(scenarios), "Runs requested": runs,
            "Expected outcomes in the pack": 0}


def _run_coverage(workspace: Workspace) -> Dict[str, object]:
    """Match the model owner's own scenario library against this benchmark."""
    owner = workspace.artifact_path("coverage", "owner_scenarios")
    if not owner:
        raise ValueError("Upload the model owner's own scenario library first.")

    intake_path = workspace.artifact_path("intake", "workbook")
    result = map_coverage(str(intake_path), str(workspace.root / REGISTRY), str(owner),
                          str(workspace.root / OVERLAP))
    workspace.state("coverage").artifacts["report"] = OVERLAP

    return {"Benchmark scenarios": result.benchmark,
            "Covered by their testing": result.covered,
            "Not covered": result.gaps,
            "Their scenarios read": result.owner_scenarios}


# Every stage that does work has a runner. The two that only take input from the user -- the
# document pack and the intake workbook -- are handled by the upload route instead.
RUNNERS: Dict[str, Callable[[Workspace], Dict[str, object]]] = {
    "evidence": _run_evidence,
    "questions": _run_questions,
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

    @app.post("/workspaces")
    def create_workspace():
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect(url_for("index"))
        workspace = Workspace.create(app.config["WORKSPACE_ROOT"], name)
        session["workspace"] = workspace.root.name
        return redirect(url_for("stage", key=STAGES[0].key))

    @app.get("/workspaces/<slug>")
    def open_workspace(slug: str):
        session["workspace"] = slug
        workspace = _workspace()
        return redirect(url_for("stage", key=workspace.current_stage().key))

    @app.get("/stage/<key>")
    def stage(key: str):
        if key not in STAGE_BY_KEY:
            abort(404)
        workspace = _workspace()

        # The graph is drawn wherever the intake is available, since it is the clearest reading
        # of what the benchmark will and will not be able to reach.
        graph_svg, graph_facts = "", {}
        if key in ("intake", "benchmark") and workspace.artifact_path("intake", "workbook"):
            try:
                intake = _intake(workspace)
                graph_svg, graph_facts = render_svg(intake), graph_summary(intake)
            except Exception as exc:                       # a malformed intake must not blank it
                logger.warning("Could not draw the graph: %s", exc)

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
            questions=_questions_for(workspace) if key == "questions" else [],
            answers=_answers_for(workspace) if key == "evidence" else [],
        )

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

    def _questions_for(workspace: Workspace):
        record = _evidence_record(workspace)
        return open_questions(record) if record else []

    @app.post("/stage/<key>/note")
    def add_note(key: str):
        """Record something the user knows that the documents did not say."""
        workspace = _workspace()
        workspace.add_note(key, request.form.get("note", ""))
        return redirect(url_for("stage", key=key, added="1"))

    @app.post("/stage/<key>/upload")
    def upload(key: str):
        """Attach a file to a stage. Intake takes a workbook; sources take the document pack."""
        workspace = _workspace()
        uploads = [f for f in request.files.getlist("files") if f and f.filename]
        if not uploads:
            return redirect(url_for("stage", key=key))

        target = workspace.root / ("sources" if key == "sources" else ".")
        target.mkdir(parents=True, exist_ok=True)

        stored = []
        for upload_file in uploads:
            name = secure_filename(upload_file.filename)
            if Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
                continue
            upload_file.save(target / name)
            stored.append(name)

        if not stored:
            workspace.mark_failed(key, "No file with a supported extension was uploaded.")
            return redirect(url_for("stage", key=key))

        if key == "intake":
            workspace.state("intake").artifacts["workbook"] = stored[0]
        elif key == "coverage":
            workspace.state("coverage").artifacts["owner_scenarios"] = stored[0]
        else:
            for name in stored:
                workspace.state(key).artifacts[name] = f"sources/{name}"

        invalidated = workspace.complete(
            key, summary={"Files": len(stored)},
            note=f"{len(stored)} file{'s' if len(stored) != 1 else ''} added.")
        workspace.save()
        return redirect(url_for("stage", key=key,
                                invalidated=", ".join(s.title for s in invalidated)))

    @app.post("/stage/<key>/run")
    def run_stage(key: str):
        workspace = _workspace()
        runner = RUNNERS.get(key)
        if runner is None:
            abort(404)
        try:
            summary = runner(workspace)
        except Exception as exc:                          # surfaced in the panel, not swallowed
            logger.exception("Stage %s failed", key)
            workspace.mark_failed(key, str(exc))
            return redirect(url_for("stage", key=key))

        invalidated = workspace.complete(key, summary=summary)
        return redirect(url_for("stage", key=key,
                                invalidated=", ".join(s.title for s in invalidated)))

    @app.post("/stage/<key>/reset")
    def reset_stage(key: str):
        workspace = _workspace()
        workspace.reset_from(key)
        return redirect(url_for("stage", key=key))

    @app.get("/stage/<key>/download/<name>")
    def download(key: str, name: str):
        workspace = _workspace()
        path = workspace.artifact_path(key, name)
        if not path:
            abort(404)
        return send_file(path, as_attachment=True)

    @app.get("/template")
    def blank_template():
        """A blank intake workbook, for a use case being described by hand."""
        workspace = _workspace()
        path = workspace.root / "intake_template.xlsx"
        write_template(str(path))
        return send_file(path, as_attachment=True)

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
