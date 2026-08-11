"""A workspace: one use case being taken through the pipeline, and the state of each stage.

Everything lives on disk. Stage outputs are the same workbooks the command line produces, and
the record of what has run is a single JSON file beside them. Two things follow, both
deliberate: closing the browser loses nothing, and anything done here can be finished from the
command line or the other way round.

The rule this module exists to enforce is that changing something early does not silently leave
stale work downstream. When a stage's inputs change, every completed stage after it is marked
out of date rather than quietly left looking finished. Their outputs are kept -- they are still
readable, and throwing away work the user might want to compare against would be its own kind of
data loss -- but the interface stops presenting them as current.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..core.representation import DEFAULT_THRESHOLD
from .stages import (COMPLETE, FAILED, LOCKED, READY, RENAMED, RUNNING, STAGE_BY_KEY,
                     STAGE_KEYS, STALE, STAGES, STOPPED, Stage, current_key, downstream_of,
                     index_of, required_before)

logger = logging.getLogger(__name__)

STATE_FILE = "workspace.json"
_SAFE_NAME = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# One lock per workspace file, shared across every Workspace instance that points at it. A
# background thread running a stage and a request handling a click each construct their own
# instance -- an instance-level lock would not stop them writing at the same moment, and ingesting
# a document pack alone runs three of those threads at once, each reporting its own progress.
_save_locks: Dict[str, threading.Lock] = {}
_save_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _save_locks_guard:
        if key not in _save_locks:
            _save_locks[key] = threading.Lock()
        return _save_locks[key]


# The runs this process actually started, as (workspace, stage) -> the id of the run that owns the
# stage. A stage's work only ever happens on a thread inside the process that marked it running,
# so this is the whole truth about whether a run recorded on disk is still alive: after a restart
# it is empty, which is precisely correct -- every run that was in flight died with the process
# hosting it. Deliberately not persisted, for the same reason the stop signals in
# :mod:`.stagecancel` are not.
#
# It holds the run id rather than only the key because a run ending has to forget *itself*: a
# thread unwinding after the stage was started again would otherwise remove the marker the newer
# run had just installed, and :meth:`Workspace._settle` would then read a genuinely running stage
# as one that died with an interrupted process.
_live_runs: Dict[Tuple[str, str], str] = {}
_live_guard = threading.Lock()


def _run_key(root: Path, stage_key: str) -> Tuple[str, str]:
    return (str(Path(root).resolve()), stage_key)


def _mark_live(root: Path, stage_key: str, run_id: str = "") -> None:
    with _live_guard:
        _live_runs[_run_key(root, stage_key)] = run_id


def _is_live(root: Path, stage_key: str) -> bool:
    with _live_guard:
        return _run_key(root, stage_key) in _live_runs


def forget_run(root: Path, stage_key: str, run_id: str = None) -> None:
    """Drop a run from the live set once it has ended, however it ended.

    Only its own: a run with no id is from before ids existed and clears the entry outright, but
    one that knows which run it is leaves a newer run's marker alone.
    """
    key = _run_key(root, stage_key)
    with _live_guard:
        if run_id is None or _live_runs.get(key) in (None, "", run_id):
            _live_runs.pop(key, None)


# How long to keep retrying a Windows file-replace that is transiently refused. OneDrive and
# antivirus scanners routinely hold a just-written file open for a fraction of a second while they
# look at it, which os.replace on Windows reports as PermissionError rather than queuing behind --
# nothing to do with actual permissions. The wait doubles each attempt because the hold is
# transient by nature; if it has not cleared within a second, something other than a momentary
# lock is wrong, and that is worth raising for rather than retrying forever.
_REPLACE_ATTEMPTS = 8
_REPLACE_BACKOFF = 0.05


def _replace(source: Path, destination: Path) -> None:
    """os.replace, tolerant of a destination something outside Python is briefly holding open.

    POSIX rename is atomic and a concurrent reader never blocks it. Windows is not: a sync client,
    an antivirus scanner, or even another process's own read can hold a file open for a moment and
    turn the replace into a PermissionError that has nothing to do with who is allowed to write
    it. Retrying briefly is the standard answer, because the hold clears on its own.
    """
    last_error = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(str(source), str(destination))
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(_REPLACE_BACKOFF * (attempt + 1))
    raise last_error


# How far through its own life a recorded stage is, for deciding which of two records to keep
# when a rename makes them collide. Anything that has run beats anything that has not.
_PROGRESSION = (LOCKED, READY, RUNNING, STOPPED, FAILED, STALE, COMPLETE)


def _further_on(candidate: dict, existing: Optional[dict]) -> bool:
    """Whether ``candidate`` is a more advanced record of a stage than ``existing``."""
    if not existing:
        return True

    def rank(entry: dict) -> int:
        status = entry.get("status", LOCKED)
        return _PROGRESSION.index(status) if status in _PROGRESSION else 0

    return rank(candidate) > rank(existing)


def slugify(name: str) -> str:
    """A filesystem-safe identifier for a use case name, stable enough to be a directory."""
    slug = _SAFE_NAME.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "use-case"


@dataclass
class StageState:
    """What has happened to one stage in one workspace."""

    status: str = LOCKED
    updated_at: str = ""
    note: str = ""
    artifacts: Dict[str, str] = field(default_factory=dict)
    summary: Dict[str, object] = field(default_factory=dict)
    progress: Dict[str, object] = field(default_factory=dict)
    run_id: str = ""
    """Which run of this stage the recorded status belongs to.

    A stage's thread writes its verdict when it unwinds, which is not always before the person
    watching has started the stage again: stop a long run, see it stop, press Run -- and the old
    thread's "stopped" could land after the new run had begun, so the new run read as stopped
    while it was still working, or as failed with the previous run's error. Every terminal write
    carries the id of the run making it and is dropped if the stage has moved on since.
    """

    @property
    def percent(self) -> int:
        """How far a running stage has got, as a whole number. Zero when it cannot be known."""
        total = self.progress.get("total") or 0
        done = self.progress.get("done") or 0
        return int(min(100, round(done / total * 100))) if total else 0

    def to_dict(self) -> dict:
        return {"status": self.status, "updated_at": self.updated_at, "note": self.note,
                "artifacts": dict(self.artifacts), "summary": dict(self.summary),
                "progress": dict(self.progress), "run_id": self.run_id}

    @classmethod
    def from_dict(cls, data: dict) -> "StageState":
        return cls(status=data.get("status", LOCKED), updated_at=data.get("updated_at", ""),
                   note=data.get("note", ""), artifacts=dict(data.get("artifacts", {})),
                   summary=dict(data.get("summary", {})),
                   progress=dict(data.get("progress", {})), run_id=data.get("run_id", ""))


class Workspace:
    """One use case, its directory, and the state of every stage in it."""

    def __init__(self, root: Path, name: str = "", created_at: str = "",
                 stages: Optional[Dict[str, StageState]] = None,
                 notes: Optional[List[dict]] = None,
                 redact_files: Optional[List[str]] = None,
                 structure_proposals: Optional[List[dict]] = None,
                 coverage_threshold: int = None,
                 coverage: Optional[dict] = None,
                 pack_gaps_only: bool = False) -> None:
        self.root = Path(root)
        self.name = name or self.root.name
        self.created_at = created_at or _now()
        self.stages: Dict[str, StageState] = stages or {
            key: StageState() for key in STAGE_KEYS}
        self.notes: List[dict] = list(notes or [])
        # Which uploaded files must be redacted before their text is read, independent of the
        # PII_REDACTION setting -- keyed "group/name" since a filename alone is not unique across
        # upload groups. A person marking one sensitive upload should not have to switch redaction
        # on for the whole pack to get it.
        self.redact_files: Set[str] = set(redact_files or [])
        # What the optional structure-review pass has proposed and not yet been applied or
        # dismissed. Applying one writes straight to the workbook -- see
        # core.intake.merge_decisions and friends -- so this list only ever holds what is
        # still open.
        self.structure_proposals: List[dict] = list(structure_proposals or [])
        # How many of the team's conversations a scenario needs before it counts as represented.
        # A judgement about how far their testing is trusted, so it belongs to the workspace and
        # to the person using it -- see core.representation.
        self.coverage_threshold: int = int(coverage_threshold or DEFAULT_THRESHOLD)
        # The last coverage run, kept so the page can show it without re-reading the workbook and
        # so changing the threshold re-reports what is already mapped rather than re-running the
        # model over every conversation again.
        self.coverage: dict = dict(coverage or {})
        # Whether the data template carries only the scenarios their testing under-covers.
        # Off by default, and deliberately so: every other stage adds to what the model owner is
        # asked for, and this is the one setting that takes things away. Narrowing the pack is a
        # decision to trust their evidence for everything left out, which is the user's to make.
        self.pack_gaps_only: bool = bool(pack_gaps_only)
        self._reconciled = self._settle()

    # ----------------------------------------------------------------- added context
    def add_note(self, stage_key: str, text: str) -> bool:
        """Record something the user knows that the documents did not say.

        Notes accumulate rather than replace, and each carries the stage it was added at. Every
        later stage that consults context sees all of them: a correction made while reading the
        evidence is just as relevant to the final review, and asking the user to repeat it there
        would be a good way to lose it.

        A blank submission is not a note. Returns whether one was actually recorded.
        """
        text = (text or "").strip()
        if not text:
            return False
        self.notes.append({"stage": stage_key, "text": text, "added_at": _now()})
        self.save()
        return True

    def notes_for(self, stage_key: str) -> List[dict]:
        return [note for note in self.notes if current_key(note["stage"]) == stage_key]

    def note_lines(self) -> List[str]:
        """Every note as one line each, attributed to the stage it was added at.

        Shared by :meth:`context_text`, which wraps these into the block the mid-pipeline passes
        take, and by anything -- like drafting the intake -- that takes notes as a list of strings
        in its own right rather than one pre-assembled block.
        """
        lines = []
        for note in self.notes:
            stage_key = current_key(note["stage"])
            title = STAGE_BY_KEY[stage_key].title if stage_key in STAGE_BY_KEY else "General"
            lines.append(f"({title}) {note['text']}")
        return lines

    def context_text(self) -> str:
        """Everything the user has added, as one block for the passes that take context.

        Each note is attributed to the stage it was added at, so a model reading this can tell a
        note written while looking at raw documents from one written while reading the finished
        scenario space.
        """
        if not self.notes:
            return ""
        return "\n".join(["NOTES ADDED BY THE VALIDATOR", ""]
                         + [f"- {line}" for line in self.note_lines()])

    # ----------------------------------------------------------------- redaction
    @staticmethod
    def _redact_key(group: str, name: str) -> str:
        return f"{group}/{name}"

    def is_marked_for_redaction(self, group: str, name: str) -> bool:
        return self._redact_key(group, name) in self.redact_files

    def set_redact(self, group: str, name: str, on: bool) -> None:
        """Mark or unmark one uploaded file for redaction ahead of the global setting.

        Does not save -- callers that change this alongside other state (invalidating a stage,
        removing the file itself) write it all in one save, the same as everywhere else a route
        makes more than one change.
        """
        key = self._redact_key(group, name)
        if on:
            self.redact_files.add(key)
        else:
            self.redact_files.discard(key)

    # ----------------------------------------------------------------- coverage
    def save_coverage(self, mappings, how_read: str = "") -> None:
        """Keep what the last coverage run found, flat enough to re-read without the model.

        Only the mappings are expensive to produce -- everything the page shows is counted from
        them. Storing them rather than the counts is what lets the representation threshold be
        moved and the answer recomputed instantly, which is the whole reason it is a setting.
        """
        self.coverage = {
            "how_read": how_read,
            "mappings": [{"conversation_id": m.conversation_id, "scenario_id": m.scenario_id,
                          "confidence": m.confidence, "intent": m.intent, "ending": m.ending,
                          "reason": m.reason, "declared_group": m.declared_group}
                         for m in mappings],
        }
        self.save()

    def coverage_mappings(self) -> List[dict]:
        return list(self.coverage.get("mappings") or [])

    def set_coverage_threshold(self, threshold: int) -> None:
        """Move the line between represented and under-represented. Does not save."""
        self.coverage_threshold = max(1, int(threshold))

    # ----------------------------------------------------------------- structure review
    def set_structure_proposals(self, proposals: List[dict]) -> None:
        """Replace the open proposals with a freshly run structure review's results.

        Each is given an id of its own here, distinct from any id the proposal names (a decision
        or state id) -- a reconnection and a consolidation can both concern the same decision, and
        a page needs one unambiguous handle per row to apply or dismiss.
        """
        self.structure_proposals = [dict(entry, id=f"sr-{index}")
                                    for index, entry in enumerate(proposals, start=1)]

    def pop_structure_proposal(self, proposal_id: str) -> Optional[dict]:
        """Remove and return one proposal by its id, or None if it is not there.

        Removed whether it is applied or dismissed: an applied proposal is now reflected in the
        workbook and would otherwise offer to be applied again, and a dismissed one has nothing
        further to say.
        """
        for entry in self.structure_proposals:
            if entry.get("id") == proposal_id:
                self.structure_proposals = [e for e in self.structure_proposals
                                            if e.get("id") != proposal_id]
                return entry
        return None

    # ----------------------------------------------------------------- persistence
    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    def save(self) -> None:
        """Write the record, atomically and one writer at a time.

        A running stage saves its progress from several threads at once -- ingesting a document
        pack reads three groups of questions in parallel, each reporting its own progress -- while
        a request handling a click can write from yet another. Writing in place means truncating
        the file first, and a reader arriving in that instant gets an empty file and a workspace
        that appears not to exist; writing beside it and renaming means a reader always sees either
        the old record or the new one. The lock is what stops two writers from racing that rename
        against each other regardless of which Workspace instance they came through, and
        :func:`_replace` covers the moment something outside Python -- a sync client, a virus
        scanner -- is holding the destination open at the same instant.
        """
        with _lock_for(self.state_path):
            self._write()

    def _payload(self) -> dict:
        return {"name": self.name, "created_at": self.created_at,
                "notes": list(self.notes),
                "redact_files": sorted(self.redact_files),
                "structure_proposals": list(self.structure_proposals),
                "coverage_threshold": self.coverage_threshold,
                "coverage": dict(self.coverage),
                "pack_gaps_only": self.pack_gaps_only,
                "stages": {k: v.to_dict() for k, v in self.stages.items()}}

    def _write(self) -> None:
        """Write the record. The caller holds the lock -- see :meth:`save` and :meth:`_commit`."""
        self.root.mkdir(parents=True, exist_ok=True)
        pending = self.state_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            pending.write_text(json.dumps(self._payload(), indent=2), encoding="utf-8")
            _replace(pending, self.state_path)
        finally:
            if pending.exists():
                pending.unlink()

    def _commit(self, key: str, run_id: Optional[str], change) -> bool:
        """Apply a run's verdict, but only if the stage has not moved on. Returns whether it did.

        Read, check and write happen together under the file lock. Checking this instance's copy
        instead would leave a window between loading the record and saving it in which another run
        could start -- and the write that followed would not merely be out of date, it would put
        the whole record back to what it was when this run loaded it, run id included.
        """
        with _lock_for(self.state_path):
            if run_id and self.state_path.exists():
                try:
                    current = json.loads(self.state_path.read_text(encoding="utf-8"))
                    recorded = current.get("stages", {}).get(key, {}).get("run_id", "")
                except (OSError, ValueError):                # unreadable: this run is all we have
                    recorded = ""
                if recorded and recorded != run_id:
                    logger.info("A finished run of %s reported after the stage was started again; "
                                "its result is dropped rather than overwriting the newer run.",
                                key)
                    return False
            change()
            self._write()
        return True

    @classmethod
    def load(cls, root: Path) -> "Workspace":
        root = Path(root)
        data = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))

        # Stages recorded under a name they have since stopped going by are read under the name
        # they go by now -- see stages.RENAMED. A workspace part-finished before the pipeline was
        # reshaped keeps every status and artifact it earned rather than opening as untouched,
        # which is what dropping the unknown keys would do. Where two old stages now share one
        # name, the further-advanced record wins: reading documents and drafting the intake became
        # one stage, and the drafting half is the one that carries the workbook.
        recorded = dict(data.get("stages", {}))
        for old, new in RENAMED.items():
            if old in recorded and _further_on(recorded[old], recorded.get(new)):
                recorded[new] = recorded[old]

        stages = {key: StageState.from_dict(recorded.get(key, {})) for key in STAGE_KEYS}
        workspace = cls(root=root, name=data.get("name", root.name),
                        created_at=data.get("created_at", ""), stages=stages,
                        notes=data.get("notes", []),
                        redact_files=data.get("redact_files", []),
                        structure_proposals=data.get("structure_proposals", []),
                        coverage_threshold=data.get("coverage_threshold"),
                        coverage=data.get("coverage", {}),
                        pack_gaps_only=data.get("pack_gaps_only", False))
        # An interrupted run was reconciled during construction -- see _settle. Written back once,
        # here, so the record on disk stops claiming something is running: after this save the
        # reconciliation finds nothing, so a page polling every second does not write every second.
        if workspace._reconciled:
            workspace.save()
        return workspace

    @classmethod
    def create(cls, base: Path, name: str) -> "Workspace":
        workspace = cls(root=Path(base) / slugify(name), name=name)
        workspace.save()
        return workspace

    @classmethod
    def list_all(cls, base: Path) -> List["Workspace"]:
        """Every workspace under a base directory, newest first."""
        base = Path(base)
        found = [cls.load(child) for child in sorted(base.glob("*"))
                 if (child / STATE_FILE).exists()]
        return sorted(found, key=lambda w: w.created_at, reverse=True)

    # ----------------------------------------------------------------- state
    def state(self, key: str) -> StageState:
        return self.stages[key]

    def _settle(self) -> bool:
        """Recompute which stages are reachable. Returns whether anything had to be reconciled.

        Only locked and ready are derived; anything that has actually run keeps the status it
        earned. A stage is ready once every *required* stage before it has produced something.
        Complete and out of date both count, since an out-of-date input is still an input and
        refusing to proceed on one would strand the workspace rather than protect it.

        Optional stages do not gate anything. That is what lets a team that already has a
        completed intake workbook open the intake stage on a fresh workspace and work forward
        from there, without pretending to read documents they were never sent.

        A stage recorded as running that this process never started is an *interrupted* run --
        see :data:`_live_runs`. Its thread died with whatever process was hosting it, so nothing
        is going to finish it or write its result, and leaving the record saying "running" strands
        the stage forever: the page polls something that will never move, and the stop button has
        no thread left to signal. It is reset here to stopped, which is exactly what it is.
        """
        reconciled = False
        for stage in STAGES:
            current = self.stages[stage.key]
            if current.status == RUNNING and not _is_live(self.root, stage.key):
                current.status, current.updated_at = STOPPED, _now()
                current.note = ("Interrupted before finishing -- the tool stopped while this was "
                                "running. Nothing partial was written; run it again.")
                current.progress = {}
                reconciled = True
                continue
            if current.status in (COMPLETE, STALE, RUNNING, FAILED, STOPPED):
                continue
            satisfied = all(self.stages[earlier.key].status in (COMPLETE, STALE)
                            for earlier in required_before(stage.key))
            current.status = READY if satisfied else LOCKED
        return reconciled

    def mark_running(self, key: str) -> str:
        """A stage has started. Returns the id of this run, which its verdict must carry back.

        Progress is written to disk so the page can read it, and so can the thread doing the work:
        the record is the one source of truth for what has happened, not anything held in memory.
        """
        state = self.stages[key]
        state.status, state.updated_at = RUNNING, _now()
        state.note = ""
        state.run_id = uuid.uuid4().hex
        state.progress = {"message": "Starting", "done": 0, "total": 0}
        _mark_live(self.root, key, state.run_id)
        self.save()
        return state.run_id

    def owns(self, key: str, run_id: Optional[str]) -> bool:
        """Whether a run's verdict is still the one this stage is waiting for.

        Read from the record rather than from this instance, which may have been loaded before
        another run started. A run with no id predates this and is trusted, so nothing that used
        to write a status has to be changed for it to keep working.
        """
        if not run_id or not self.state_path.exists():
            return True
        try:
            current = json.loads(self.state_path.read_text(encoding="utf-8"))
            recorded = current.get("stages", {}).get(key, {}).get("run_id", "")
        except (OSError, ValueError):
            return True
        return not recorded or recorded == run_id

    def report_progress(self, key: str, message: str, done: int = 0, total: int = 0) -> None:
        """Record where a running stage has got to.

        Written straight to disk rather than held in memory, because the page that displays it is
        a different request -- often a different process after a restart -- and a progress bar
        nobody can read is the same as no progress bar.
        """
        state = self.stages[key]
        state.progress = {"message": message, "done": done, "total": total}
        self.save()

    def mark_failed(self, key: str, note: str, run_id: str = None) -> None:
        def change():
            state = self.stages[key]
            state.status, state.note, state.updated_at = FAILED, note, _now()
            state.progress = {}

        if self._commit(key, run_id, change):
            forget_run(self.root, key, run_id)

    def mark_stopped(self, key: str, run_id: str = None) -> None:
        """A stage was asked to stop and unwound before finishing.

        Nothing it would have written was, so the stage sits exactly where it was before this run
        started -- distinct from a failure, and just as ready to be run again.
        """
        def change():
            state = self.stages[key]
            state.status, state.updated_at = STOPPED, _now()
            state.note, state.progress = "Stopped before finishing.", {}

        if self._commit(key, run_id, change):
            forget_run(self.root, key, run_id)

    def complete(self, key: str, artifacts: Optional[Dict[str, str]] = None,
                 summary: Optional[Dict[str, object]] = None, note: str = "",
                 run_id: str = None) -> List[Stage]:
        """Record a stage as done and mark everything downstream of it out of date.

        Returns the stages that were invalidated, so the interface can say what just happened
        rather than leaving the user to notice on their own.
        """
        invalidated: List[Stage] = []

        def change():
            nonlocal invalidated
            state = self.stages[key]
            state.status = COMPLETE
            state.updated_at = _now()
            state.note = note
            state.progress = {}
            if artifacts:
                state.artifacts.update(artifacts)
            if summary is not None:
                state.summary = dict(summary)
            invalidated = self.invalidate_after(key)
            self._settle()

        if not self._commit(key, run_id, change):
            return []
        forget_run(self.root, key, run_id)
        return invalidated

    def invalidate_after(self, key: str) -> List[Stage]:
        """Mark every completed stage after this one as out of date. Outputs are kept."""
        return self._invalidate(downstream_of(key))

    def invalidate_from(self, key: str) -> List[Stage]:
        """Mark this stage and everything after it out of date. Outputs are kept.

        For a change to a stage's own *input* rather than to something upstream of it. Uploading
        a corrected intake workbook is the case that matters: the scenario space built from the old one
        is out of date, which :meth:`invalidate_after` already said, but so is the intake stage's
        own report of what the workbook declares -- and leaving that showing "5 decision points"
        beside a workbook that now declares seven is the more misleading of the two, because it
        reads as a fact about the file rather than as a stale figure.
        """
        return self._invalidate([STAGE_BY_KEY[key]] + downstream_of(key))

    def _invalidate(self, stages: List[Stage]) -> List[Stage]:
        invalidated = []
        for stage in stages:
            if self.stages[stage.key].status == COMPLETE:
                self.stages[stage.key].status = STALE
                self.stages[stage.key].updated_at = _now()
                invalidated.append(stage)
        return invalidated

    def reset_from(self, key: str, delete: Optional[List[str]] = None) -> List[str]:
        """Clear this stage and everything after it, for starting a branch of work again.

        Clearing the status is not the same as clearing the work. Several stages read what they
        need straight off disk rather than through the record, so a reset that only forgets the
        status leaves the old metadata workbook, evidence and questions where they were and they come
        straight back on the next run. Pass ``delete`` to remove them as well.

        Submitted documents are never touched either way. They are input rather than output, and
        each has its own remove.
        """
        for stage in [STAGE_BY_KEY[key]] + downstream_of(key):
            self.stages[stage.key] = StageState()

        removed = []
        for name in delete or []:
            path = (self.root / name).resolve()
            if self.root.resolve() in path.parents and path.is_file():
                path.unlink()
                removed.append(name)

        self._settle()
        self.save()
        return removed

    # ----------------------------------------------------------------- queries
    def can_run(self, key: str) -> bool:
        return self.stages[key].status in (READY, STALE, COMPLETE, FAILED, STOPPED)

    def is_blocked(self, key: str) -> bool:
        return self.stages[key].status == LOCKED

    def has_output(self, key: str) -> bool:
        return self.stages[key].status in (COMPLETE, STALE)

    def current_stage(self) -> Stage:
        """Where the user should be taken: the first stage that still needs attention."""
        for stage in STAGES:
            if self.stages[stage.key].status in (READY, RUNNING, FAILED, STOPPED):
                return stage
        for stage in STAGES:
            if self.stages[stage.key].status == STALE:
                return stage
        return STAGES[-1]

    def progress(self) -> Dict[str, int]:
        """Counts for the rail's header. Out-of-date stages are not counted as done."""
        done = sum(1 for s in STAGES if self.stages[s.key].status == COMPLETE)
        stale = sum(1 for s in STAGES if self.stages[s.key].status == STALE)
        required = sum(1 for s in STAGES if not s.optional)
        return {"done": done, "stale": stale, "total": len(STAGES), "required": required}

    def artifact_path(self, key: str, name: str) -> Optional[Path]:
        """Resolve a recorded artifact, guarding against anything outside the workspace."""
        recorded = self.stages[key].artifacts.get(name)
        if not recorded:
            return None
        path = (self.root / recorded).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            return None
        return path if path.exists() else None


def stage_view(workspace: Workspace, stage: Stage) -> dict:
    """Everything a template needs about one stage, assembled here rather than in the markup."""
    state = workspace.state(stage.key)
    return {
        "stage": stage,
        "number": index_of(stage.key) + 1,
        "status": state.status,
        "state": state,
        "is_current": workspace.current_stage().key == stage.key,
        "has_output": workspace.has_output(stage.key),
    }
