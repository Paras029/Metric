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
from pathlib import Path
from typing import Callable, Dict

from flask import (Flask, abort, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.utils import secure_filename

from ..core.intake import read_intake, write_template
from ..io import write_challenge_pack, write_registry
from ..pipeline import build_scenarios
from .stages import STAGE_BY_KEY, STAGES, STATUS_LABELS, index_of
from .workspace import Workspace, stage_view

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path.cwd() / "workspaces"
UPLOAD_EXTENSIONS = {".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg", ".md", ".txt", ".xlsx"}


# --------------------------------------------------------------------------- stage runners
def _run_intake(workspace: Workspace) -> Dict[str, object]:
    """Read the intake workbook and report the shape of what it declares."""
    path = workspace.artifact_path("intake", "workbook")
    if not path:
        raise ValueError("No intake workbook has been provided yet.")
    intake = read_intake(str(path))
    return {"Use case": intake.name, "Capabilities": len(intake.capabilities),
            "Decision points": len(intake.decisions), "States": len(intake.states),
            "Personas": len(intake.personas), "Tools": len(intake.tools)}


def _run_benchmark(workspace: Workspace) -> Dict[str, object]:
    """Enumerate every route through the declared graph and add the applicable probes."""
    path = workspace.artifact_path("intake", "workbook")
    if not path:
        raise ValueError("Complete the intake stage first.")
    intake = read_intake(str(path))
    scenarios = build_scenarios(intake, with_probes=True)

    registry = workspace.root / "registry.xlsx"
    write_registry(str(registry), intake, scenarios)
    workspace.state("benchmark").artifacts["registry"] = registry.name

    probes = sum(1 for s in scenarios if s.is_probe)
    return {"Scenarios": len(scenarios), "Routes through the graph": len(scenarios) - probes,
            "Probes": probes}


def _run_issue(workspace: Workspace) -> Dict[str, object]:
    """Write the challenge pack for the model owner and the registry kept internally."""
    intake_path = workspace.artifact_path("intake", "workbook")
    if not intake_path:
        raise ValueError("Complete the intake stage first.")
    intake = read_intake(str(intake_path))
    scenarios = build_scenarios(intake, with_probes=True)

    pack = workspace.root / "challenge_pack.xlsx"
    registry = workspace.root / "registry.xlsx"
    write_challenge_pack(str(pack), intake, scenarios)
    write_registry(str(registry), intake, scenarios)
    workspace.state("issue").artifacts.update(
        {"challenge_pack": pack.name, "registry": registry.name})
    return {"Scenarios issued": len(scenarios), "Expected outcomes in the pack": 0}


# Stages with no runner yet render an honest "not connected" panel rather than a button that
# quietly does nothing.
RUNNERS: Dict[str, Callable[[Workspace], Dict[str, object]]] = {
    "intake": _run_intake,
    "benchmark": _run_benchmark,
    "issue": _run_issue,
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
        return render_template(
            "stage.html",
            workspace=workspace,
            rail=[stage_view(workspace, s) for s in STAGES],
            view=stage_view(workspace, STAGE_BY_KEY[key]),
            runnable=key in RUNNERS,
            invalidated=request.args.get("invalidated", ""),
        )

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
