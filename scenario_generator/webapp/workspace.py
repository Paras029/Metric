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
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .stages import (COMPLETE, FAILED, LOCKED, READY, RUNNING, STAGE_BY_KEY, STAGE_KEYS,
                     STALE, STAGES, STOPPED, Stage, downstream_of, index_of, required_before)

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


# The runs this process actually started, as (workspace, stage). A stage's work only ever happens
# on a thread inside the process that marked it running, so this set is the whole truth about
# whether a run recorded on disk is still alive: after a restart it is empty, which is precisely
# correct -- every run that was in flight died with the process that was hosting it. Deliberately
# not persisted, for the same reason the stop signals in :mod:`.stagecancel` are not.
_live_runs: Set[Tuple[str, str]] = set()
_live_guard = threading.Lock()


def _run_key(root: Path, stage_key: str) -> Tuple[str, str]:
    return (str(Path(root).resolve()), stage_key)


def _mark_live(root: Path, stage_key: str) -> None:
    with _live_guard:
        _live_runs.add(_run_key(root, stage_key))


def _is_live(root: Path, stage_key: str) -> bool:
    with _live_guard:
        return _run_key(root, stage_key) in _live_runs


def forget_run(root: Path, stage_key: str) -> None:
    """Drop a run from the live set once it has ended, however it ended."""
    with _live_guard:
        _live_runs.discard(_run_key(root, stage_key))


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

    @property
    def percent(self) -> int:
        """How far a running stage has got, as a whole number. Zero when it cannot be known."""
        total = self.progress.get("total") or 0
        done = self.progress.get("done") or 0
        return int(min(100, round(done / total * 100))) if total else 0

    def to_dict(self) -> dict:
        return {"status": self.status, "updated_at": self.updated_at, "note": self.note,
                "artifacts": dict(self.artifacts), "summary": dict(self.summary),
                "progress": dict(self.progress)}

    @classmethod
    def from_dict(cls, data: dict) -> "StageState":
        return cls(status=data.get("status", LOCKED), updated_at=data.get("updated_at", ""),
                   note=data.get("note", ""), artifacts=dict(data.get("artifacts", {})),
                   summary=dict(data.get("summary", {})),
                   progress=dict(data.get("progress", {})))


class Workspace:
    """One use case, its directory, and the state of every stage in it."""

    def __init__(self, root: Path, name: str = "", created_at: str = "",
                 stages: Optional[Dict[str, StageState]] = None,
                 notes: Optional[List[dict]] = None,
                 pending: Optional[List[dict]] = None,
                 redact_files: Optional[List[str]] = None,
                 structure_proposals: Optional[List[dict]] = None) -> None:
        self.root = Path(root)
        self.name = name or self.root.name
        self.created_at = created_at or _now()
        self.stages: Dict[str, StageState] = stages or {
            key: StageState() for key in STAGE_KEYS}
        self.notes: List[dict] = list(notes or [])
        # Intake edits sketched in the interface and not yet written to the workbook. Held here
        # rather than in the workbook so the workbook stays the one authority for what the
        # benchmark is built from, and a half-finished idea cannot reach it.
        self.pending: List[dict] = list(pending or [])
        # Which uploaded files must be redacted before their text is read, independent of the
        # PII_REDACTION setting -- keyed "group/name" since a filename alone is not unique across
        # upload groups. A person marking one sensitive upload should not have to switch redaction
        # on for the whole pack to get it.
        self.redact_files: Set[str] = set(redact_files or [])
        # What the optional structure-review pass has proposed and not yet been applied or
        # dismissed. Unlike ``pending``, applying one of these writes straight to the workbook --
        # see core.intake.merge_decisions and friends -- so this list only ever holds what is
        # still open, not a sketch waiting on a separate commit.
        self.structure_proposals: List[dict] = list(structure_proposals or [])
        self._reconciled = self._settle()

    # ----------------------------------------------------------------- added context
    def add_note(self, stage_key: str, text: str, question: str = "") -> None:
        """Record something the user knows that the documents did not say.

        Notes accumulate rather than replace, and each carries the stage it was added at. Every
        later stage that consults context sees all of them: a correction made while reading the
        evidence is just as relevant to the final review, and asking the user to repeat it there
        would be a good way to lose it.
        """
        self.add_notes([(question, text)], stage_key)

    def add_notes(self, entries: List[tuple], stage_key: str) -> int:
        """Record several answers at once, as (question, text) pairs. Returns how many landed.

        Answering questions one at a time meant a page reload between each, which turns a list of
        six into six round trips. Nothing here requires the whole list: blanks are skipped, so a
        person can settle what they know now, come back, and settle the rest later, and each pass
        registers what it carried.
        """
        added = 0
        for question, text in entries:
            text = (text or "").strip()
            if not text:
                continue
            self.notes.append({"stage": stage_key, "text": text, "added_at": _now(),
                               "question": (question or "").strip()})
            added += 1
        if added:
            self.save()
        return added

    def answered_questions(self) -> Dict[str, str]:
        """Every open question a person has answered, newest answer winning."""
        return {note["question"]: note["text"]
                for note in self.notes if note.get("question")}

    def notes_for(self, stage_key: str) -> List[dict]:
        return [note for note in self.notes if note["stage"] == stage_key]

    def note_lines(self) -> List[str]:
        """Every note as one line each, attributed to the stage it was added at.

        Shared by :meth:`context_text`, which wraps these into the block the mid-pipeline passes
        take, and by anything -- like drafting the intake -- that takes notes as a list of strings
        in its own right rather than one pre-assembled block.
        """
        lines = []
        for note in self.notes:
            title = STAGE_BY_KEY[note["stage"]].title if note["stage"] in STAGE_BY_KEY else "General"
            if note.get("question"):
                lines.append(f"({title}) Q: {note['question']} — A: {note['text']}")
            else:
                lines.append(f"({title}) {note['text']}")
        return lines

    def context_text(self) -> str:
        """Everything the user has added, as one block for the passes that take context.

        Each note is attributed to the stage it was added at, so a model reading this can tell a
        note written while looking at raw documents from one written while reading the finished
        benchmark.
        """
        if not self.notes:
            return ""
        return "\n".join(["NOTES ADDED BY THE VALIDATION TEAM", ""]
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
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"name": self.name, "created_at": self.created_at,
                   "notes": list(self.notes), "pending": list(self.pending),
                   "redact_files": sorted(self.redact_files),
                   "structure_proposals": list(self.structure_proposals),
                   "stages": {k: v.to_dict() for k, v in self.stages.items()}}

        with _lock_for(self.state_path):
            pending = self.state_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                pending.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                _replace(pending, self.state_path)
            finally:
                if pending.exists():
                    pending.unlink()

    @classmethod
    def load(cls, root: Path) -> "Workspace":
        root = Path(root)
        data = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
        stages = {key: StageState.from_dict(data.get("stages", {}).get(key, {}))
                  for key in STAGE_KEYS}
        workspace = cls(root=root, name=data.get("name", root.name),
                        created_at=data.get("created_at", ""), stages=stages,
                        notes=data.get("notes", []), pending=data.get("pending", []),
                        redact_files=data.get("redact_files", []),
                        structure_proposals=data.get("structure_proposals", []))
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

    def mark_running(self, key: str) -> None:
        """A stage has started. Its progress is written to disk so the page can read it."""
        _mark_live(self.root, key)
        state = self.stages[key]
        state.status, state.updated_at = RUNNING, _now()
        state.note = ""
        state.progress = {"message": "Starting", "done": 0, "total": 0}
        self.save()

    def report_progress(self, key: str, message: str, done: int = 0, total: int = 0) -> None:
        """Record where a running stage has got to.

        Written straight to disk rather than held in memory, because the page that displays it is
        a different request -- often a different process after a restart -- and a progress bar
        nobody can read is the same as no progress bar.
        """
        state = self.stages[key]
        state.progress = {"message": message, "done": done, "total": total}
        self.save()

    def mark_failed(self, key: str, note: str) -> None:
        forget_run(self.root, key)
        state = self.stages[key]
        state.status, state.note, state.updated_at = FAILED, note, _now()
        state.progress = {}
        self.save()

    def mark_stopped(self, key: str) -> None:
        """A stage was asked to stop and unwound before finishing.

        Nothing it would have written was, so the stage sits exactly where it was before this run
        started -- distinct from a failure, and just as ready to be run again.
        """
        forget_run(self.root, key)
        state = self.stages[key]
        state.status, state.note, state.updated_at = STOPPED, "Stopped before finishing.", _now()
        state.progress = {}
        self.save()

    def complete(self, key: str, artifacts: Optional[Dict[str, str]] = None,
                 summary: Optional[Dict[str, object]] = None, note: str = "") -> List[Stage]:
        """Record a stage as done and mark everything downstream of it out of date.

        Returns the stages that were invalidated, so the interface can say what just happened
        rather than leaving the user to notice on their own.
        """
        forget_run(self.root, key)
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
        self.save()
        return invalidated

    def invalidate_after(self, key: str) -> List[Stage]:
        """Mark every completed stage after this one as out of date. Outputs are kept."""
        return self._invalidate(downstream_of(key))

    def invalidate_from(self, key: str) -> List[Stage]:
        """Mark this stage and everything after it out of date. Outputs are kept.

        For a change to a stage's own *input* rather than to something upstream of it. Uploading
        a corrected intake workbook is the case that matters: the benchmark built from the old one
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
        status leaves the old registry, evidence and questions where they were and they come
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
