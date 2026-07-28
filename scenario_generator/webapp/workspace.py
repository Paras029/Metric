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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .stages import (COMPLETE, FAILED, LOCKED, READY, RUNNING, STAGE_BY_KEY, STAGE_KEYS,
                     STALE, STAGES, Stage, downstream_of, index_of, required_before)

STATE_FILE = "workspace.json"
_SAFE_NAME = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
                 notes: Optional[List[dict]] = None) -> None:
        self.root = Path(root)
        self.name = name or self.root.name
        self.created_at = created_at or _now()
        self.stages: Dict[str, StageState] = stages or {
            key: StageState() for key in STAGE_KEYS}
        self.notes: List[dict] = list(notes or [])
        self._settle()

    # ----------------------------------------------------------------- added context
    def add_note(self, stage_key: str, text: str, question: str = "") -> None:
        """Record something the user knows that the documents did not say.

        Notes accumulate rather than replace, and each carries the stage it was added at. Every
        later stage that consults context sees all of them: a correction made while reading the
        evidence is just as relevant to the final review, and asking the user to repeat it there
        would be a good way to lose it.
        """
        text = (text or "").strip()
        if not text:
            return
        self.notes.append({"stage": stage_key, "text": text, "added_at": _now(),
                           "question": (question or "").strip()})
        self.save()

    def answered_questions(self) -> Dict[str, str]:
        """Every open question a person has answered, newest answer winning."""
        return {note["question"]: note["text"]
                for note in self.notes if note.get("question")}

    def notes_for(self, stage_key: str) -> List[dict]:
        return [note for note in self.notes if note["stage"] == stage_key]

    def context_text(self) -> str:
        """Everything the user has added, as one block for the passes that take context.

        Each note is attributed to the stage it was added at, so a model reading this can tell a
        note written while looking at raw documents from one written while reading the finished
        benchmark.
        """
        if not self.notes:
            return ""
        lines = ["NOTES ADDED BY THE VALIDATION TEAM", ""]
        for note in self.notes:
            title = STAGE_BY_KEY[note["stage"]].title if note["stage"] in STAGE_BY_KEY else "General"
            if note.get("question"):
                lines.append(f"- ({title}) Q: {note['question']}")
                lines.append(f"  A: {note['text']}")
            else:
                lines.append(f"- ({title}) {note['text']}")
        return "\n".join(lines)

    # ----------------------------------------------------------------- persistence
    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    def save(self) -> None:
        """Write the record, atomically.

        A running stage saves its progress every few seconds from a background thread while the
        page polls for it from a request thread. Writing in place means truncating the file first,
        and a reader arriving in that instant gets an empty file and a workspace that appears not
        to exist. Writing beside it and renaming means a reader sees either the old record or the
        new one, never a half-written one -- ``os.replace`` is atomic on both platforms this runs
        on.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"name": self.name, "created_at": self.created_at,
                   "notes": list(self.notes),
                   "stages": {k: v.to_dict() for k, v in self.stages.items()}}

        pending = self.state_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            pending.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(str(pending), str(self.state_path))
        finally:
            if pending.exists():
                pending.unlink()

    @classmethod
    def load(cls, root: Path) -> "Workspace":
        root = Path(root)
        data = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
        stages = {key: StageState.from_dict(data.get("stages", {}).get(key, {}))
                  for key in STAGE_KEYS}
        return cls(root=root, name=data.get("name", root.name),
                   created_at=data.get("created_at", ""), stages=stages,
                   notes=data.get("notes", []))

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

    def _settle(self) -> None:
        """Recompute which stages are reachable.

        Only locked and ready are derived; anything that has actually run keeps the status it
        earned. A stage is ready once every *required* stage before it has produced something.
        Complete and out of date both count, since an out-of-date input is still an input and
        refusing to proceed on one would strand the workspace rather than protect it.

        Optional stages do not gate anything. That is what lets a team that already has a
        completed intake workbook open the intake stage on a fresh workspace and work forward
        from there, without pretending to read documents they were never sent.
        """
        for stage in STAGES:
            current = self.stages[stage.key]
            if current.status in (COMPLETE, STALE, RUNNING, FAILED):
                continue
            satisfied = all(self.stages[earlier.key].status in (COMPLETE, STALE)
                            for earlier in required_before(stage.key))
            current.status = READY if satisfied else LOCKED

    def mark_running(self, key: str) -> None:
        """A stage has started. Its progress is written to disk so the page can read it."""
        state = self.stages[key]
        state.status, state.updated_at = RUNNING, _now()
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
        state = self.stages[key]
        state.status, state.note, state.updated_at = FAILED, note, _now()
        state.progress = {}
        self.save()

    def complete(self, key: str, artifacts: Optional[Dict[str, str]] = None,
                 summary: Optional[Dict[str, object]] = None, note: str = "") -> List[Stage]:
        """Record a stage as done and mark everything downstream of it out of date.

        Returns the stages that were invalidated, so the interface can say what just happened
        rather than leaving the user to notice on their own.
        """
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
        invalidated = []
        for stage in downstream_of(key):
            if self.stages[stage.key].status == COMPLETE:
                self.stages[stage.key].status = STALE
                self.stages[stage.key].updated_at = _now()
                invalidated.append(stage)
        return invalidated

    def reset_from(self, key: str) -> None:
        """Clear this stage and everything after it, for starting a branch of work again."""
        for stage in [STAGE_BY_KEY[key]] + downstream_of(key):
            self.stages[stage.key] = StageState()
        self._settle()
        self.save()

    # ----------------------------------------------------------------- queries
    def can_run(self, key: str) -> bool:
        return self.stages[key].status in (READY, STALE, COMPLETE, FAILED)

    def is_blocked(self, key: str) -> bool:
        return self.stages[key].status == LOCKED

    def has_output(self, key: str) -> bool:
        return self.stages[key].status in (COMPLETE, STALE)

    def current_stage(self) -> Stage:
        """Where the user should be taken: the first stage that still needs attention."""
        for stage in STAGES:
            if self.stages[stage.key].status in (READY, RUNNING, FAILED):
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
