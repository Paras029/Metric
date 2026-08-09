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
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file,
                   session, url_for)
from werkzeug.utils import secure_filename

from ..core.evidence import FACETS
from ..core.gaps import find_gaps
from ..core.intake import read_review_notes, set_decision_scope, write_template
from ..core.models import MATERIALITY
from ..ingest import open_questions
from ..ingest.context_document import FACET_HEADINGS, FACET_QUESTIONS
from ..ingest.conversations import read_conversations
from ..ingest.groups import (ALL_EXTENSIONS, DEFAULT_GROUP, GROUP_BY_KEY, GROUPS, MODEL_DOC,
                             OWNER_SCENARIOS, SUPPORTING, folder_for, files_in, remove_file)
from ..io import read_registry, read_scenarios, write_coverage_report, write_registry
from ..llm import metering
from ..llm.cancellation import Stopped
from ..llm.structure_review import review_structure
from ..pipeline import revise_intake_workbook
from . import stagecancel
from .coverageview import coverage_view, stored_mappings, stored_report
from .graphview import graph_summary, render_svg
from .runners import (CONTEXT, DRAFT_INTAKE, EVIDENCE, OVERLAP, REGISTRY, RUNNERS,
                      STAGE_OUTPUTS,
                      _apply_proposal, _context, _evidence_record, _intake, _proposal_dicts,
                      _scenarios, _snapshot)
from .scenarios import FILTER_FIELDS, PAGE_SIZE, build_rows
from .stages import RUNNING, STAGE_BY_KEY, STAGES, STATUS_LABELS, downstream_of, index_of
from .workspace import Workspace, stage_view

# Stages whose output is a set of scenarios, so the page shows them rather than only counts.
SCENARIO_STAGES = ("text", "materiality", "review", "coverage", "issue")

# Stages where reading the decision graph is the work rather than a reference, so it is drawn at
# full width in the page. Everywhere else it goes in the side panel at thumbnail size.
GRAPH_STAGES = ("intake", "benchmark")

# Groups redaction can actually do something to: the two that carry text ingestion reads. A
# diagram has no text to redact, and the model owner's own conversations never reach
# build_corpus at all -- they are read separately, only to measure coverage, at a later stage.
REDACTABLE_GROUPS = (MODEL_DOC, SUPPORTING)

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path.cwd() / "workspaces"
UPLOAD_EXTENSIONS = set(ALL_EXTENSIONS) | {".xlsx", ".xlsm", ".csv"}


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
        intake, intake_problem = None, ""
        if workspace.artifact_path("intake", "workbook"):
            try:
                intake = _intake(workspace)
            except Exception as exc:                       # a malformed intake must not blank it
                logger.warning("Could not read the intake: %s", exc)
                intake_problem = str(exc)

        # The graph is drawn wherever the intake is available, since it is the clearest reading
        # of what the benchmark will and will not be able to reach. Two placements, one drawing:
        # full width in the page on the stages where reading it *is* the work, and small in the
        # side panel everywhere after, so what the benchmark was built from stays in view without
        # anyone navigating back for it.
        graph_svg, graph_facts, aside_graph = "", {}, ""
        if intake is not None:
            try:
                graph_facts = graph_summary(intake)
                if key in GRAPH_STAGES:
                    graph_svg = render_svg(intake)
                else:
                    aside_graph = render_svg(intake)
            except Exception as exc:
                # Said on the page rather than only in the log. A graph that cannot be drawn takes
                # its whole section with it, controls included, and a section that is simply not
                # there reads as a broken interface rather than as a broken declaration.
                logger.exception("Could not draw the graph")
                intake_problem = intake_problem or f"The graph could not be drawn: {exc}"

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
            # Every note, not only this stage's. A note added while reading the documents is
            # given to every stage after it, so showing only the ones typed here would hide the
            # thing that is actually informing the run in front of you.
            notes=_notes_with_origin(workspace),
            note_total=len(workspace.notes),
            graph_svg=graph_svg,
            graph_facts=graph_facts,
            intake_problem=intake_problem,
            aside_graph=aside_graph,
            aside_files=_submitted_files(workspace),
            decisions=decisions,
            structure_proposals=workspace.structure_proposals if key == "intake" else [],
            structure_review_available=key == "intake" and intake is not None,
            intake_questions=_intake_questions_for(workspace, intake)
                if key == "intake" else None,
            answered=workspace.answered_questions(),
            answers=_answers_for(workspace) if key == "documents" else [],
            groups=_group_rows(workspace, key),
            benchmark=_benchmark_for(workspace, key, intake),
            coverage=_coverage_for(workspace, key, intake),
            pack_gaps_only=workspace.pack_gaps_only,
            materiality_tiers=MATERIALITY,
            produced={n: p for n, p in workspace.state(key).artifacts.items()
                      if not str(p).startswith("sources/")},
        )

    def _notes_with_origin(workspace: Workspace):
        """Every note, newest first, each saying which stage it was added at.

        The stage matters because it dates the note against the work: a correction typed while
        reading the documents and one typed after seeing the benchmark are different kinds of
        remark, and both are handed to every stage that follows.
        """
        rows = []
        for note in reversed(workspace.notes):
            stage = STAGE_BY_KEY.get(note.get("stage", ""))
            rows.append(dict(note, origin=stage.title if stage else "General"))
        return rows

    def _submitted_files(workspace: Workspace):
        """Everything submitted so far, by heading, for the side panel.

        A read-only tally rather than the uploader: what it answers is "did that vendor document
        ever get added?", which is worth being able to check from stage six without walking back
        to stage one. Adding and removing stay on the documents stage, where the drop targets say
        what each heading is for.
        """
        rows = []
        for group in GROUPS:
            files = files_in(workspace.root, group.key)
            if files:
                rows.append({"title": group.title, "icon": group.icon,
                            "names": [f.name for f in files]})
        return rows

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
                # time to ask them for a clearer file. A transcript file that cannot be split into
                # turns is the failure that matters, and it is invisible until someone looks.
                try:
                    found, how = read_conversations(files[0])
                    plural = "" if len(found) == 1 else "s"
                    row["note"] = f"{len(found)} conversation{plural} found — {how}"
                except Exception as exc:
                    row["note"] = f"This cannot be read as conversations: {exc}"
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
        except Exception:
            # Never blank the page over one unreadable registry -- but log the traceback rather
            # than the message alone. A malformed workbook and a mistake in this module both
            # arrive here, and only one of them is diagnosable from "could not read".
            logger.exception("Could not read the benchmark for display")
            return None
        filters = {field: request.args.get(f"filter_{field}", "") for field in FILTER_FIELDS}
        return build_rows(scenarios, request.args.get("view", "attention"), stage=key,
                          filters=filters, limit=_page_limit())

    def _coverage_for(workspace: Workspace, key: str, intake):
        """What the model owner's conversations covered, at the threshold currently set.

        Recounted on every page view from the stored mappings rather than read back from the run's
        own summary, so moving the threshold changes what is shown immediately. See
        :mod:`.coverageview`.
        """
        if key not in ("coverage", "issue") or intake is None:
            return None
        path = workspace.root / REGISTRY
        if not path.exists() or not workspace.coverage_mappings():
            return None
        try:
            report = stored_report(workspace, read_registry(str(path)))
            texts = {s.id: s.description for s in read_scenarios(str(path), intake)}
        except Exception:
            logger.exception("Could not rebuild the coverage report for display")
            return None
        return coverage_view(workspace, report, texts) if report else None

    def _page_limit() -> Optional[int]:
        """How many rows to render, from ``?limit=``: a number, ``all``, or the default."""
        raw = request.args.get("limit", "")
        if raw == "all":
            return None
        if raw.isdigit():
            return int(raw)
        return PAGE_SIZE

    def _answers_for(workspace: Workspace):
        """One row per question, with the whole reading behind it.

        The summary line answers "was this question answered, and how well"; the detail answers
        "what did it actually say". Both belong on the page, but a page carrying eleven full
        readings at once is a page nobody reads, so the detail is folded away until asked for.

        ``points`` and ``unknowns`` are the two halves of one reading and are named as such here
        rather than left as bare counts: a *point* is a specific thing the documents establish
        about this question, and an *unknown* is something they were asked and did not settle.
        """
        record = _evidence_record(workspace)
        if not record:
            return []
        rows = []
        for facet in FACETS:
            answer = record.answer_for(facet)
            sources = record.claims_by_id() if answer else {}
            rows.append({
                "heading": FACET_HEADINGS.get(facet, facet),
                "question": FACET_QUESTIONS.get(facet, ""),
                "answered": bool(answer and answer.is_answered),
                "confidence": answer.confidence if answer else "Low",
                "points": list(answer.points) if answer else [],
                "unknowns": list(answer.unknowns) if answer else [],
                "answer": (answer.answer if answer else "") or "",
                # What the answer was actually built from, so a reader can go and check it. The
                # quote is what was found in the source; the statement is the reading of it.
                "sources": [{"statement": sources[claim_id].statement,
                            "quote": sources[claim_id].quote,
                            "where": str(sources[claim_id].source)}
                           for claim_id in (answer.sources if answer else [])
                           if claim_id in sources],
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

        def _row(question: str, why: str, heading: str = "", example: str = "") -> dict:
            return {"heading": heading, "question": question, "why": why, "example": example,
                   "answered": question in answered}

        grouped: Dict[str, list] = {}
        cross_cutting = []
        for gap in find_gaps(intake):
            row = _row(gap.question, gap.why, gap.heading, gap.example)
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
                # The drafter's note *is* the question here -- it says what it was unsure of --
                # so the heading names the row and the note carries the ask, rather than a
                # manufactured "is this right?" that says nothing about what to check.
                question = (f"Confirm or correct {field}" if field
                            else "Confirm or correct what the draft was unsure of")
                row = _row(question, text, match.group(1).upper() if match else field,
                           example="Yes, that is right — or the correction")
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
            # An upload that happens to carry the drafter's own filename would be taken for the
            # tool's file and revised over. Stored under another name so "never overwrite what
            # somebody uploaded" holds on the filename alone, which is what decides it.
            if stored[0] == DRAFT_INTAKE:
                renamed = f"uploaded_{DRAFT_INTAKE}"
                (workspace.root / stored[0]).replace(workspace.root / renamed)
                stored[0] = renamed
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

        # Revised into the tool's own file rather than over the input. Where the current workbook
        # is one somebody uploaded, that upload is the only copy of their work, and a revision
        # writing over it leaves nothing to go back to. The workspace moves on to the revision;
        # the original stays on disk and stays downloadable.
        extracted = workspace.root / CONTEXT
        target = workspace.root / DRAFT_INTAKE
        revise_intake_workbook(
            str(path), str(target),
            context_path=str(extracted) if extracted.exists() else None,
            evidence_path=str(workspace.root / EVIDENCE),
            notes=workspace.note_lines())
        workspace.state("intake").artifacts["workbook"] = DRAFT_INTAKE

        invalidated = workspace.invalidate_from("intake")
        workspace.save()
        logger.info("Revised %s into %s with %d note(s).",
                    path.name, DRAFT_INTAKE, len(workspace.notes))
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

    @app.route("/stage/coverage/threshold", methods=["POST"])
    def set_coverage_threshold():
        """Move the line between represented and under-represented, and re-report on the spot.

        Deliberately not a re-run. The mappings are what the model produced and they do not change
        with the threshold -- only the verdict drawn through them does -- so this recounts what is
        already stored and rewrites the workbook from it. Answering "what if we asked for three
        conversations each?" should cost a page load, not another pass over every transcript.
        """
        workspace = _workspace()
        raw = (request.form.get("threshold") or "").strip()
        if raw.isdigit():
            workspace.set_coverage_threshold(int(raw))

        mappings = stored_mappings(workspace)
        path = workspace.root / REGISTRY
        if mappings and path.exists():
            intake = _intake(workspace)
            benchmark = read_registry(str(path))
            texts = {s.id: s.description for s in read_scenarios(str(path), intake)}
            report = stored_report(workspace, benchmark)
            write_coverage_report(str(workspace.root / OVERLAP), report, mappings, texts)
            # The pack's contents can depend on this line -- see runners._run_issue -- so a pack
            # written under the old threshold is no longer what this workspace would issue.
            if workspace.pack_gaps_only:
                workspace.invalidate_after("coverage")

        workspace.save()
        return redirect(url_for("stage", key="coverage", _anchor="coverage"))

    @app.route("/stage/issue/scope", methods=["POST"])
    def set_pack_scope():
        """Choose whether the challenge pack carries the whole benchmark or only the gaps.

        Off by default. Every other stage widens what the model owner is asked to run, and this is
        the one control that narrows it: leaving a scenario out says the model owner's own
        conversations are evidence enough for it. That is a judgement about how far the model
        owner's testing is trusted, so it is asked for explicitly rather than applied because
        coverage happens to have run.
        """
        workspace = _workspace()
        workspace.pack_gaps_only = bool(request.form.get("on"))
        invalidated = workspace.invalidate_from("issue")
        workspace.save()
        return redirect(url_for("stage", key="issue",
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

        run_id = workspace.mark_running(key)
        root = workspace.root
        # Registered here, in the request that starts the run, rather than inside the thread it
        # starts -- so a stop clicked in the instant after this returns always finds a signal
        # waiting for it, rather than racing the new thread to create one.
        event = stagecancel.start(root, key)
        threading.Thread(target=_execute, args=(root, key, event, run_id), daemon=True).start()
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/stop", methods=["POST"])
    def stop_stage(key: str):
        """Ask a running stage to stop. Calls already sent finish; nothing further is sent.

        The stage is left exactly as it was before this run -- nothing partial is written -- so
        it comes back as ready to run again rather than as failed.

        Where nothing is listening, the stage is marked stopped here instead. That is not a
        failure to stop: it means the thread that was running it is already gone, so there is
        nothing left to signal and the record is simply out of date. Without this a stage left
        running by an interrupted process could be clicked at forever with no effect.
        """
        workspace = _workspace()
        if not stagecancel.stop(workspace.root, key):
            if workspace.state(key).status == RUNNING:
                workspace.mark_stopped(key)
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

    @app.template_filter("when")
    def when(stamp: str) -> str:
        """A stored timestamp as something a person reads without decoding it.

        Timestamps are recorded as UTC in ISO form, which is the right thing to store -- sortable,
        unambiguous, and the same string on every machine -- and the wrong thing to show:
        ``2026-08-08T14:23:11+00:00`` is a value, not a time of day. This is the fallback text,
        rendered server-side so the page is readable with no script at all. The script in
        stage.html then re-renders it in whatever timezone the browser is actually in, which is
        the one the reader thinks in.
        """
        try:
            moment = datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            return str(stamp or "")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.strftime("%d %b %Y at %H:%M UTC")

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from ..llm import config

    try:
        config.check_tuning()
    except config.BrokenTuningFile as exc:
        print(f"\n{exc}\n\nFix that line and run again.\n")
        raise SystemExit(2)
    app = create_app()
    print("\n  Scenario generator — http://127.0.0.1:5000\n")
    print(f"  Settings: {config.tuning_path()} — edits apply to the next call, no restart\n")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()


def _execute(root: Path, key: str, cancel, run_id: str = None) -> None:
    """Run one stage on a background thread, reporting progress as it goes.

    The workspace is re-read here rather than handed across the thread boundary, so the record on
    disk stays the one source of truth for what has happened -- the same record the polling
    request reads, and the same one the command line would read.

    ``run_id`` identifies this run, and every verdict below carries it. A thread unwinding is not
    always faster than the person watching: stop a long run, see it stop, press Run again, and
    this thread's "stopped" could otherwise land on top of the run that had already started. See
    :meth:`Workspace.owns`.

    ``cancel`` is the stop signal ``run_stage`` registered before this thread was started, and
    *every* runner takes it -- not only the ones that make many model calls. Handing it to all of
    them is what makes stopping mean the same thing everywhere: a stage with nothing long to
    interrupt simply finds it unset, which costs nothing and is a great deal easier to reason
    about than a list of which stages honour it.
    """
    workspace = Workspace.load(root)

    def report(message: str, done: int = 0, total: int = 0) -> None:
        workspace.report_progress(key, message, done, total)

    try:
        with metering.counted() as calls:
            summary = RUNNERS[key](workspace, progress=report, cancel=cancel)
        if calls():
            summary = dict(summary, **{"Model calls": calls()})
            logger.info("Stage %s finished in %d model call(s).", key, calls())
    except Stopped:
        # Nothing this run would have written was: the pass raises before its caller reaches the
        # write. The workspace is exactly where it was before the run started.
        logger.info("Stage %s stopped by the user.", key)
        Workspace.load(root).mark_stopped(key, run_id=run_id)
        return
    except Exception as exc:                              # surfaced in the panel, not swallowed
        logger.exception("Stage %s failed", key)
        Workspace.load(root).mark_failed(key, str(exc), run_id=run_id)
        return
    finally:
        stagecancel.clear(root, key, cancel)

    finished = Workspace.load(root)
    if not finished.owns(key, run_id):
        return
    finished.stages[key].artifacts.update(workspace.stages[key].artifacts)
    finished.complete(key, summary=summary, run_id=run_id)


def _refusal(names) -> str:
    """Why nothing was stored, named precisely enough to act on."""
    if not names:
        return "No file was uploaded."
    supported = ", ".join(sorted(UPLOAD_EXTENSIONS))
    return (f"Nothing was stored. {', '.join(names)} — this reads {supported}. A .doc or .xls from "
            f"an older Office version needs saving as .docx or .xlsx first.")
