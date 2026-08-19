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

from ..core.gaps import CAPABILITY, DECISION, STATE, find_gaps
from ..core.intake import (read_review_notes, set_capability_span, set_decision_scope,
                           write_template)
from ..core.models import MATERIALITY
from ..ingest.conversations import read_conversations
from ..ingest.extraction import SAME_FLOW_EACH, SPLIT_ACROSS_IMAGES
from ..ingest.groups import (ALL_EXTENSIONS, DEFAULT_GROUP, DIAGRAMS, GROUP_BY_KEY, GROUPS,
                             MODEL_DOC, OWNER_SCENARIOS, SUPPORTING, folder_for, files_in,
                             remove_file)
from ..io import read_space_metadata, read_scenarios, write_coverage_report, write_space_metadata
from ..llm import metering
from ..llm.cancellation import Stopped
from ..llm.structure_review import review_structure
from ..pipeline import revise_intake_workbook
from . import stagecancel
from .coverageview import coverage_view, stored_mappings, stored_report
from .graphpage import render_graph_page
from .graphview import (declaration as _declaration, graph_summary, render_blocks_svg,
                        render_svg,
                        routes as _graph_routes)
from .runners import (CONTEXT, DRAFT_INTAKE, EVIDENCE, OVERLAP, METADATA, RUNNERS,
                      STAGE_OUTPUTS,
                      _apply_proposal, _context, _intake, _proposal_dicts,
                      _scenarios, _snapshot)
from .scenarios import PAGE_SIZE, build_rows, parse_cells, shape
from .stages import RUNNING, STAGE_BY_KEY, STAGES, STATUS_LABELS, downstream_of, index_of
from .workspace import Workspace, stage_view

# Stages whose output is a set of scenarios, so the page shows them rather than only counts.
SCENARIO_STAGES = ("scenarios", "variations", "materiality", "review", "coverage",
                   "summary")

# Stages where reading the decision graph is the work rather than a reference, so it is drawn at
# full width in the page. Everywhere else it goes in the side panel at thumbnail size.
#
# Every stage that lists scenarios draws it too, because a scenario *is* a route through it and
# opening a card lights that route -- which needs a picture big enough to read, not the thumbnail.
# Those stages start it folded away: the list is what somebody came to the materiality stage for,
# and a full graph above three hundred cards pushes all of them off the screen.
GRAPH_STAGES = ("intake", "workflow") + SCENARIO_STAGES
GRAPH_OPEN_STAGES = ("intake", "workflow")

# Groups redaction can actually do something to: the two that carry text ingestion reads. A
# diagram has no text to redact, and the model owner's conversations never reach
# build_corpus at all -- they are read separately, only to measure coverage, at a later stage.
# Every group whose files are text a model will read. The transcripts belong here most of
# all: the documentation describes an agent, where these are real conversations with real
# customers in them.
REDACTABLE_GROUPS = (MODEL_DOC, SUPPORTING, OWNER_SCENARIOS)

# Which stage actually reads each kind of submission, and therefore which one a file arriving in
# it makes stale.
#
# Only the model owner's conversations differ from the default, and they differ for a reason that
# is load-bearing: they are deliberately kept out of the evidence corpus -- see
# ``ingest.groups.EVIDENCE_GROUPS`` -- because reading the model owner's testing as evidence about
# the agent would let their blind spots into the scenario space by the back door. Nothing before
# coverage reads them, so nothing before coverage can be out of date because one arrived. Treating
# every upload as intake evidence meant dropping in a transcript at the coverage stage marked six
# finished stages stale, to re-derive a byte-identical result.
STAGE_THAT_READS: Dict[str, str] = {OWNER_SCENARIOS: "coverage"}

# How several submitted workflow images relate, and how each choice reads on the page. Asked only
# where there is more than one image: with one there is nothing to relate, and the reading takes a
# shorter path that never consults this.
#
# It is asked rather than inferred because getting it wrong is quiet and expensive. Stitching two
# drawings of one flow welds the end of the first onto the start of the second and enumerates
# routes the agent does not have; reconciling genuine pieces folds the end of one picture into the
# start of the next as "the same step under a different label". Neither is visible in the result
# without reading the whole graph against the pictures, and the person who uploaded them knows.
DIAGRAM_MODE_LABELS = {
    SPLIT_ACROSS_IMAGES: "One workflow, split across these images",
    SAME_FLOW_EACH: "Each image shows the same workflow",
}

# The three kinds of row the declared graph is made of, in the order they are worth reading: a
# decision naming no outcomes enumerates nothing at all, which is more urgent than a capability
# that will merely be probed less thoroughly than its neighbours. Questions about anything else --
# what the pack never said about policy, testing or vocabulary -- are not asked of a person here;
# they are in the context document, as a remark on the pack rather than a task with a name on it.
_GRAPH_ROW_KINDS = {DECISION: "Decisions", STATE: "States", CAPABILITY: "Capabilities"}

# A review note's own "field" text is free-form -- whatever the model wrote, not a schema this can
# rely on -- so only an unambiguous row id is trusted to place it. Ids loose enough to false-match
# ordinary words (a persona's "P1" against any word starting with a "p") are not looked for.
_ROW_ID_IN_TEXT = re.compile(r"\b(DEC-\d+|S-\d+|CAP-\d+)\b", re.I)
_KIND_BY_PREFIX = {"DEC": DECISION, "S": STATE, "CAP": CAPABILITY}

# Whether the structure review is offered on the intake page. Off: what the review should be
# allowed to propose, and how far a proposal may reach into a declaration the model owner signed
# off, is not settled, and a half-defined tidy-up applied to the graph everything downstream is
# built from is worse than no tidy-up. The machinery behind it is complete and tested; this is the
# one switch that puts it back on the page once the scope is written down.
STRUCTURE_REVIEW_OFFERED = False

# The group name the "upload a completed intake" drop sends. Not one of the submission headings:
# an intake workbook is the declaration itself rather than evidence for one, so it goes to the
# workspace root and is never read as a document.
INTAKE_GROUP = "intake_workbook"

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
        return render_template("index.html", stages=STAGES,
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

        # Read once and pass it on. Both the graph and the scenario space panel need it, and reading a
        # workbook twice to render one page is a cost paid on every navigation.
        intake, intake_problem = None, ""
        if workspace.artifact_path("intake", "workbook"):
            try:
                intake = _intake(workspace)
            except Exception as exc:                       # a malformed intake must not blank it
                logger.warning("Could not read the intake: %s", exc)
                intake_problem = str(exc)

        # The graph is drawn wherever the intake is available, since it is the clearest reading
        # of what the scenario space will and will not be able to reach. Two placements, one drawing:
        # full width in the page on the stages where reading it *is* the work, and small in the
        # side panel everywhere after, so what the scenario space was built from stays in view without
        # anyone navigating back for it.
        graph_svg, graph_facts, aside_graph, blocks_svg = "", {}, "", ""
        if intake is not None:
            try:
                graph_facts = graph_summary(intake)
                if key in GRAPH_STAGES:
                    graph_svg = render_svg(intake)
                    # Empty unless spans are drawn, which the template reads as "there is no
                    # block view to offer" rather than as an empty picture to show.
                    blocks_svg = render_blocks_svg(intake)
                else:
                    aside_graph = render_svg(intake)
            except Exception as exc:
                # Said on the page rather than only in the log. A graph that cannot be drawn takes
                # its whole section with it, controls included, and a section that is simply not
                # there reads as a broken interface rather than as a broken declaration.
                logger.exception("Could not draw the graph")
                intake_problem = intake_problem or f"The graph could not be drawn: {exc}"

        # Read once, shown twice: the list in the middle of the page and the tally in the side
        # panel are two readings of the same scenarios, and reading the workbook again for the
        # second is both slower and a way for the two to disagree.
        scenarios = _stage_scenarios(workspace, key, intake)

        # Every already-declared decision, with whether it is walked -- shown only on the intake
        # stage, and only for what the workbook already has. A sketched decision is not here yet
        # to have a scope one way or the other.
        decisions, capabilities, tools, state_options = [], [], [], []
        if key == "intake" and intake is not None:
            decisions, capabilities, tools, state_options = _declaration(intake)

        return render_template(
            "stage.html",
            workspace=workspace,
            rail=[stage_view(workspace, s) for s in STAGES],
            view=stage_view(workspace, STAGE_BY_KEY[key]),
            runnable=key in RUNNERS,
            invalidated=request.args.get("invalidated", ""),
            intake_group=INTAKE_GROUP,
            # Every note, not only this stage's. A note added while reading the documents is
            # given to every stage after it, so showing only the ones typed here would hide the
            # thing that is actually informing the run in front of you.
            notes=_notes_with_origin(workspace),
            note_total=len(workspace.notes),
            graph_svg=graph_svg,
            blocks_svg=blocks_svg,
            graph_facts=graph_facts,
            intake_problem=intake_problem,
            aside_graph=aside_graph,
            aside_files=_submitted_files(workspace),
            decisions=decisions,
            capabilities=capabilities,
            state_options=state_options,
            tools=tools,
            questions=_declaration_questions(workspace, intake) if key == "intake" else [],
            graph_open=key in GRAPH_OPEN_STAGES,
            routes=_routes(intake, scenarios) if graph_svg else {},
            structure_proposals=(workspace.structure_proposals
                                 if STRUCTURE_REVIEW_OFFERED and key == "intake" else []),
            structure_review_available=(STRUCTURE_REVIEW_OFFERED and key == "intake"
                                        and intake is not None),
            groups=_group_rows(workspace, key),
            space=_space_for(scenarios, key, workspace),
            shape=_shape_for(scenarios, key),
            coverage=_coverage_for(workspace, key, intake),
            coverage_shape=_coverage_shape(workspace),
            pack_gaps_only=workspace.pack_gaps_only,
            materiality_tiers=MATERIALITY,
            produced={n: p for n, p in workspace.state(key).artifacts.items()
                      if not str(p).startswith("sources/")},
        )

    def _notes_with_origin(workspace: Workspace):
        """Every note, newest first, each saying which stage it was added at.

        The stage matters because it dates the note against the work: a correction typed while
        reading the documents and one typed after seeing the scenario space are different kinds of
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

        The coverage stage shows only the model owner's scenarios: it is the one thing that
        stage consumes, and the rest of the pack is not its business.
        """
        if key == "intake":
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
                              if workspace.is_marked_for_redaction(group.key, f.name)},
                  # Only with more than one picture: one image is read in a single pass that never
                  # asks how it relates to anything, and a control asking how it relates to itself
                  # is one that makes a reader wonder what they have missed.
                  "modes": (DIAGRAM_MODE_LABELS if group.key == DIAGRAMS and len(files) > 1
                            else None),
                  "mode": workspace.diagram_mode}
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

    def _stage_scenarios(workspace: Workspace, key: str, intake):
        """The scenarios as this stage left them, once there are any.

        A stage reads its own snapshot rather than the live metadata workbook, so coming back to the
        scenario-text page after materiality has run shows the text as it was written, not the
        text with tiers assigned afterwards beside it. Falling back to the live metadata workbook covers
        a workspace built before snapshots existed, and the stage that has not run yet.
        """
        if key not in SCENARIO_STAGES or intake is None:
            return None
        path = workspace.root / _snapshot(key)
        if not path.exists():
            path = workspace.root / METADATA
        if not path.exists():
            return None
        try:
            return read_scenarios(str(path), intake)
        except Exception:
            # Never blank the page over one unreadable metadata workbook -- but log the traceback rather
            # than the message alone. A malformed workbook and a mistake in this module both
            # arrive here, and only one of them is diagnosable from "could not read".
            logger.exception("Could not read the scenario space for display")
            return None

    def _capability_names(workspace: Workspace) -> Dict[str, str]:
        """Capability id to name, so a scenario can say which block of the agent it tests.

        By id where the name is blank, and empty where the declaration cannot be read: naming the
        block is worth having and is never worth failing a page over.
        """
        try:
            return {c.id: c.name or c.id for c in _intake(workspace).capabilities}
        except Exception:
            return {}

    def _space_for(scenarios, key: str, workspace: Optional[Workspace] = None):
        """The list in the middle of the page: this stage's scenarios, narrowed to the cells
        picked in the grid above it. ``?cell=`` repeats, one per selected cell."""
        if scenarios is None:
            return None
        return build_rows(scenarios, stage=key, cells=parse_cells(request.args.getlist("cell")),
                          limit=_page_limit(),
                          capability_names=_capability_names(workspace) if workspace else None)

    def _shape_for(scenarios, key: str):
        """The tally in the side panel, from the same scenarios the list is drawn from.

        Reading both off one snapshot is the point: a panel that counted the live metadata workbook while
        the list showed a stage's own snapshot would put two different totals on the same screen
        and leave no way to tell which was the scenario space.
        """
        if not scenarios:
            return None
        return shape(scenarios, stage=key)

    def _coverage_shape(workspace: Workspace) -> Optional[Dict[str, object]]:
        """What the coverage stage found, small enough for the panel and shown on every stage.

        Only the counts that change a decision: how much of the model owner's evidence landed
        anywhere, and how much of the scenario space it reached. It stays in the panel after the
        coverage stage has been navigated away from because it is the one thing that says how
        much of this scenario space the model owner has already exercised, and that bears on every
        judgement made about the scenario space, not only on the stage that measured it.

        Counted from the stored mappings at whatever threshold is set now, the same as the stage's
        own page, so the two never disagree.
        """
        path = workspace.root / METADATA
        if not path.exists() or not workspace.coverage_mappings():
            return None
        try:
            report = stored_report(workspace, read_space_metadata(str(path)))
        except Exception:
            logger.exception("Could not count coverage for the side panel")
            return None
        if not report:
            return None
        summary = report.summary()
        return {
            "threshold": report.threshold,
            "conversations": summary["Conversations read"],
            "mapped": summary["Mapped to a scenario"],
            "unmatched": summary["Matched no scenario"],
            "counted": summary["Scenarios in the space"],
            "represented": summary["Represented"],
            "under": summary["Under-represented"],
            "untouched": summary["Never exercised"],
        }

    def _coverage_for(workspace: Workspace, key: str, intake):
        """What the model owner's conversations covered, at the threshold currently set.

        Recounted on every page view from the stored mappings rather than read back from the run's
        own summary, so moving the threshold changes what is shown immediately. See
        :mod:`.coverageview`.
        """
        if key not in ("coverage", "summary") or intake is None:
            return None
        path = workspace.root / METADATA
        if not path.exists() or not workspace.coverage_mappings():
            return None
        try:
            report = stored_report(workspace, read_space_metadata(str(path)))
            texts = {s.id: s.description for s in read_scenarios(str(path), intake)}
        except Exception:
            logger.exception("Could not rebuild the coverage report for display")
            return None
        return coverage_view(workspace, report, texts) if report else None

    def _routes(intake, scenarios) -> dict:
        """Where each scenario runs in the drawing, for the card that opens it to light it up.

        Never allowed to take the page down with it. A scenario whose route the drawing cannot
        place is a real disagreement worth knowing about, but the list and the graph are both
        still worth reading without it, and a stack trace where a page should be is not how to
        report a mismatch between two views of the same declaration.
        """
        if intake is None or not scenarios:
            return {}
        try:
            return _graph_routes(intake, scenarios)
        except Exception:
            logger.exception("Could not work out where the scenarios run in the graph")
            return {}

    def _page_limit() -> Optional[int]:
        """How many rows to render, from ``?limit=``: a number, ``all``, or the default."""
        raw = request.args.get("limit", "")
        if raw == "all":
            return None
        if raw.isdigit():
            return int(raw)
        return PAGE_SIZE

    def _declaration_questions(workspace: Workspace, intake) -> list:
        """What the declared graph still needs, asked of the row that needs it.

        Deliberately only the three kinds of row the graph is made of: a capability, a decision,
        a state. Everything the reading left open used to be here too -- what the documents never
        said about policy, about how the model owner tested, about domain vocabulary -- and it
        buried the handful of questions that actually stop a branch being walked under a much
        longer list nobody could finish. Those readings are not lost: they are in the context
        document and the evidence file, where they are a remark on how complete the pack is rather
        than a task with somebody's name on it.

        The questions here all share one property, and it is the reason they are the ones worth a
        person's time: unanswered, a part of the graph cannot be built, so a branch goes untested
        and nothing downstream says so. A decision naming no outcomes enumerates nothing. A state
        nothing reaches is a route that stops. An untyped capability drops its probes silently.

        Answers become notes carrying their question -- see :meth:`Workspace.add_notes` -- so a
        re-run reads them as answers rather than as loose remarks, and nothing has to be re-typed
        into the workbook by hand.
        """
        if intake is None:
            return []

        answered = workspace.answered_questions()

        def row(question: str, why: str, heading: str = "", example: str = "") -> dict:
            return {"heading": heading, "question": question, "why": why, "example": example,
                    "answer": answered.get(question, "")}

        # Narrowed in exactly one place -- the loop that builds the groups, below -- so gaps of
        # every kind are collected here and the three that get asked about are chosen once. Two
        # filters for one decision is how the second one comes to disagree with the first.
        grouped: Dict[str, list] = {}
        for gap in find_gaps(intake):
            grouped.setdefault(gap.kind, []).append(
                row(gap.question, gap.why, gap.heading, gap.example))

        # The drafter's own hedges, read back from the workbook it wrote. A softer signal than a
        # structural gap -- the row is filled in, but not with confidence, and only the model that
        # wrote it knows why -- and it is placed by row id, so one that names no row is dropped
        # rather than guessed at.
        path = workspace.artifact_path("intake", "workbook")
        if path:
            for note in read_review_notes(str(path)):
                field, text = str(note.get("field", "")), str(note.get("note", ""))
                match = _ROW_ID_IN_TEXT.search(field) or _ROW_ID_IN_TEXT.search(text)
                if not text or not match:
                    continue
                kind = _KIND_BY_PREFIX[match.group(1).upper().split("-")[0]]
                # The note *is* the question: it says what the draft was unsure of, which is more
                # use than a manufactured "is this right?" that names nothing to check.
                grouped.setdefault(kind, []).append(
                    row(f"Confirm or correct {field}" if field
                        else "Confirm or correct what the draft was unsure of",
                        text, match.group(1).upper(),
                        example="Yes, that is right — or the correction"))

        return [{"label": label, "rows": grouped[kind]}
                for kind, label in _GRAPH_ROW_KINDS.items() if grouped.get(kind)]

    @app.route("/stage/<key>/note", methods=["POST"])
    def add_note(key: str):
        """Record something the user knows that the documents did not say.

        The stage does not need re-running for this to count: the note joins the context every
        following stage receives, and is attributed to the stage it was written at.
        """
        workspace = _workspace()
        workspace.add_note(key, request.form.get("note", ""))
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/answers", methods=["POST"])
    def save_answers(key: str):
        """Save several answers at once, and accept a partial pass.

        The questions are a list, and a list answered one item at a time is a page reload per
        item. Everything filled in is saved together; everything left blank is left open, so a
        person can settle what they know now and come back for the rest. What has been answered
        stays editable -- a second thought about an answer is worth more than the first one.
        """
        workspace = _workspace()
        entries = []
        for field in request.form:
            if not field.startswith("answer-"):
                continue
            entries.append((request.form.get(f"question-{field[len('answer-'):]}", ""),
                            request.form.get(field, "")))

        saved = workspace.add_notes(entries, key)
        logger.info("Recorded %d answer(s) at the %s stage.", saved, key)
        return redirect(url_for("stage", key=key, _anchor="questions"))

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

        # Two different things arrive on the intake stage now that reading and drafting are one:
        # the model owner's documentation, which is filed under its heading like any other
        # submission, and a completed intake workbook, which *is* the declaration and goes to the
        # root. The group is what tells them apart -- the intake-workbook drop sends INTAKE_GROUP,
        # everything else names a real heading.
        group = request.form.get("group", "") or (
            OWNER_SCENARIOS if key == "coverage" else DEFAULT_GROUP)
        as_intake = group == INTAKE_GROUP
        if as_intake:
            target = workspace.root
        else:
            group = group if group in GROUP_BY_KEY else DEFAULT_GROUP
            target = folder_for(workspace.root, group)
        target.mkdir(parents=True, exist_ok=True)

        # What this heading takes, not what the tool takes anywhere. A card that says ".png .jpg
        # .jpeg" and then quietly accepts a PDF is a card that lies: the file is stored under
        # "Workflow diagrams", is read as an ordinary document because that is what it is, and
        # nothing on the page ever says the diagram nobody can find was never a diagram.
        accepted = (set(UPLOAD_EXTENSIONS) if as_intake
                    else set(GROUP_BY_KEY[group].accepts))

        stored, refused = [], []
        for upload_file in uploads:
            name = secure_filename(upload_file.filename)
            if Path(name).suffix.lower() not in accepted:
                refused.append(upload_file.filename)
                continue
            upload_file.save(str(target / name))
            stored.append(name)

        if not stored:
            workspace.mark_failed(key, _refusal(refused, accepted, "" if as_intake
                                                else GROUP_BY_KEY[group].title))
            return redirect(url_for("stage", key=key))

        reader = "intake" if as_intake else STAGE_THAT_READS.get(group, "intake")
        if as_intake:
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
                workspace.state(reader).artifacts[name] = f"sources/{group}/{name}"

        # A new file invalidates the stage that reads it as well as everything built on top: that
        # stage has not seen this file, so its own reported result is out of date. Only that stage
        # onward, though -- a file no earlier stage reads cannot have made any of them wrong.
        invalidated = workspace.invalidate_from(reader)
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
            reader = STAGE_THAT_READS.get(group, "intake")
            workspace.state(reader).artifacts.pop(Path(name).name, None)
            workspace.set_redact(group, name, False)
            workspace.invalidate_from(reader)
            workspace.save()
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/diagrams", methods=["POST"])
    def set_diagram_mode(key: str):
        """Say how the submitted workflow images relate to one another.

        Invalidates the intake only when it changes: it is an input to the reading, so it makes a
        completed reading out of date the same way a new file would, and choosing what was already
        chosen has changed nothing.
        """
        workspace = _workspace()
        chosen = request.form.get("mode", "")
        chosen = chosen if chosen in DIAGRAM_MODE_LABELS else SPLIT_ACROSS_IMAGES
        if chosen != workspace.diagram_mode:
            workspace.diagram_mode = chosen
            workspace.invalidate_from("intake")
            workspace.save()
        return redirect(url_for("stage", key=key))

    @app.route("/stage/<key>/redact", methods=["POST"])
    def toggle_redact(key: str):
        """Mark or unmark one uploaded file to be redacted ahead of the global setting.

        Takes effect the next time the stage that reads the file runs -- this only records the
        choice. Changing it invalidates that stage the same way adding a file does: what it will be
        given is different now, whether or not the file itself changed. Only that stage onward,
        since a file no earlier stage reads cannot have made any of them wrong.
        """
        workspace = _workspace()
        group, name = request.form.get("group", ""), request.form.get("name", "")
        if group in REDACTABLE_GROUPS and name:
            workspace.set_redact(group, name, bool(request.form.get("on")))
            workspace.invalidate_from(STAGE_THAT_READS.get(group, "intake"))
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

    @app.route("/stage/intake/capability/<capability_id>/span", methods=["POST"])
    def set_span(capability_id: str):
        """Set which states one capability is entered in and which it hands on or finishes at.

        This is the whole of how a capability gets a span: nothing proposes one, because where a
        block of the agent ends is a judgement about the agent rather than something readable off
        the graph. It is set here, against the drawing, and takes effect on the next run.
        """
        workspace = _workspace()
        path = workspace.artifact_path("intake", "workbook")
        if not path:
            abort(404)
        entries = request.form.getlist("entry")
        exits = request.form.getlist("exit")
        if set_capability_span(str(path), capability_id, entries, exits):
            invalidated = workspace.invalidate_from("intake")
            workspace.save()
            return redirect(url_for("stage", key="intake",
                                    invalidated=", ".join(s.title for s in invalidated))
                            + "#capabilities")
        return redirect(url_for("stage", key="intake") + "#capabilities")

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
        one call, not the dozens a document pack or a whole scenario space can take, so there is
        nothing here for a progress bar to usefully report on.
        """
        if not STRUCTURE_REVIEW_OFFERED:
            abort(404)
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
        if not STRUCTURE_REVIEW_OFFERED:
            abort(404)
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
        if not STRUCTURE_REVIEW_OFFERED:
            abort(404)
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
        path = workspace.root / METADATA
        if mappings and path.exists():
            intake = _intake(workspace)
            space = read_space_metadata(str(path))
            texts = {s.id: s.description for s in read_scenarios(str(path), intake)}
            report = stored_report(workspace, space)
            write_coverage_report(str(workspace.root / OVERLAP), report, mappings, texts)
            # The pack's contents can depend on this line -- see runners._run_issue -- so a pack
            # written under the old threshold is no longer what this workspace would issue.
            if workspace.pack_gaps_only:
                workspace.invalidate_after("coverage")

        workspace.save()
        return redirect(url_for("stage", key="coverage", _anchor="coverage"))

    @app.route("/stage/summary/scope", methods=["POST"])
    def set_pack_scope():
        """Choose whether the data template carries the whole scenario space or only the gaps.

        Off by default. Every other stage widens what the model owner is asked to run, and this is
        the one control that narrows it: leaving a scenario out says the model owner's
        conversations are evidence enough for it. That is a judgement about how far the model
        owner's testing is trusted, so it is asked for explicitly rather than applied because
        coverage happens to have run.
        """
        workspace = _workspace()
        workspace.pack_gaps_only = bool(request.form.get("on"))
        invalidated = workspace.invalidate_from("summary")
        workspace.save()
        return redirect(url_for("stage", key="summary",
                                invalidated=", ".join(s.title for s in invalidated)))

    @app.route("/stage/<key>/scenario/<scenario_id>", methods=["POST"])
    def rule_on_scenario(key: str, scenario_id: str):
        """Record the reviewer's ruling on one scenario, straight into the scenario space metadata.

        Two rulings, both already columns the scenario space metadata carries. An override sets materiality and
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

        write_space_metadata(str(workspace.root / METADATA), intake, scenarios)

        # The template's variation counts come from materiality, so a pack written before this ruling no
        # longer reflects it. Saying so beats letting a stale workbook look current.
        workspace.invalidate_after("review")
        workspace.save()
        return redirect(url_for("stage", key=key, cell=request.form.getlist("cell"),
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

    @app.route("/graph", methods=["GET"])
    def graph_page():
        """The declared graph on its own, as one self-contained file.

        A validation report has to carry the graph, and so does any conversation with the model
        owner about a branch nobody declared -- and neither of those can be had over a screenshot,
        which loses the zoom the moment a graph is large enough to need one. This is the same
        drawing with the same reading controls, in a file that works with nothing running: the
        stylesheet and the script are inlined rather than fetched, so it survives being emailed,
        attached, or opened from a share months later.

        ``?download=1`` sends it as an attachment. Without it the page opens in the browser, which
        is what somebody who only wants a bigger view of it is asking for.
        """
        workspace = _workspace()
        if not workspace.artifact_path("intake", "workbook"):
            abort(404)
        intake = _intake(workspace)
        # Built from the workbooks, by the same function the command line calls -- so correcting a
        # row and building it again corrects the page, and the two front ends cannot produce
        # different pictures of the same declaration.
        scenarios = []
        if (workspace.root / METADATA).exists():
            try:
                scenarios = read_scenarios(str(workspace.root / METADATA), intake)
            except Exception:
                logger.exception("Could not read the scenario space for the graph page")
        page = render_graph_page(intake, scenarios)

        if not request.args.get("download"):
            return page
        path = workspace.root / "declared_graph.html"
        path.write_text(page, encoding="utf-8")
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
    print("\n  METRIC — http://127.0.0.1:5000\n")
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


def _refusal(names, accepted=None, heading: str = "") -> str:
    """Why nothing was stored, named precisely enough to act on.

    Says what *this heading* takes rather than what the tool takes, since that is the choice in
    front of the person: a PDF refused by the workflow-diagrams card belongs under a different
    heading, not in a different format.
    """
    if not names:
        return "No file was uploaded."
    supported = ", ".join(sorted(accepted or UPLOAD_EXTENSIONS))
    where = f'"{heading}" takes' if heading else "this reads"
    note = (" Anything else describing the agent goes under one of the other headings."
            if heading else
            " A .doc or .xls from an older Office version needs saving as .docx or .xlsx first.")
    return f"Nothing was stored. {', '.join(names)} — {where} {supported}.{note}"
