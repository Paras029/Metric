"""Edits to the intake that have been sketched but not committed.

Correcting an intake means editing a workbook, and a workbook edit is a round trip: change it,
save it, upload it, look at the graph, discover it was wrong, start again. That is a slow way to
answer a question as simple as "does this branch belong here?"

So additions live here first. A decision or a state added in the interface is held against the
workspace, merged into the intake for display only, and drawn dashed. One that has been attached
to something appears where it will land; one that has not is parked below the graph -- the visible
difference between an edit that is ready and an edit that still needs somewhere to go. Committing
writes them into the workbook and empties the buffer.

Nothing here changes the intake until that commit. The workbook stays the single authority for
what the benchmark is built from, which is the property that makes the benchmark defensible; this
is a sketch pad in front of it, not a second source of truth.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.models import Decision, IntakeData, State

DECISION = "decision"
STATE = "state"


@dataclass
class PendingItem:
    """One decision or state a person has sketched but not yet committed."""

    kind: str
    id: str
    name: str = ""
    # Decisions
    capability: str = ""
    outcomes: List[str] = field(default_factory=list)
    input_source: str = "User"
    reached_from: str = ""
    """For a decision: the already-declared state that will lead to it.

    Attaching a new decision means the state before it has to name the decision as one of its
    next steps, which is a change to a row that already exists rather than a new row. It is held
    here until the commit, which is the only point at which the workbook is touched.
    """

    # States
    reached_via: str = ""
    next_decisions: List[str] = field(default_factory=list)
    is_terminal: bool = False
    outcome_type: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PendingItem":
        known = {f: data.get(f) for f in cls.__dataclass_fields__ if f in data}
        known.setdefault("kind", DECISION)
        known.setdefault("id", "")
        return cls(**{k: v for k, v in known.items() if v is not None})


def next_id(prefix: str, existing: List[str]) -> str:
    """The next free identifier in the intake's own numbering, so ids stay readable."""
    used = set()
    for identifier in existing:
        tail = str(identifier).rsplit("-", 1)[-1]
        if tail.isdigit():
            used.add(int(tail))
    number = 1
    while number in used:
        number += 1
    width = 2 if prefix == "DEC" else 2
    return f"{prefix}-{number:0{width}d}"


class PendingEdits:
    """The sketch buffer for one workspace."""

    def __init__(self, items: Optional[List[PendingItem]] = None) -> None:
        self.items: List[PendingItem] = list(items or [])

    # ----------------------------------------------------------------- persistence
    def to_list(self) -> List[dict]:
        return [item.to_dict() for item in self.items]

    @classmethod
    def from_list(cls, data) -> "PendingEdits":
        return cls([PendingItem.from_dict(entry) for entry in (data or [])
                    if isinstance(entry, dict)])

    # ----------------------------------------------------------------- editing
    def add(self, item: PendingItem) -> PendingItem:
        self.items = [existing for existing in self.items if existing.id != item.id]
        self.items.append(item)
        return item

    def remove(self, identifier: str) -> bool:
        before = len(self.items)
        self.items = [item for item in self.items if item.id != identifier]
        return len(self.items) < before

    def clear(self) -> None:
        self.items = []

    def by_kind(self, kind: str) -> List[PendingItem]:
        return [item for item in self.items if item.kind == kind]

    def ids(self, kind: str) -> List[str]:
        return [item.id for item in self.by_kind(kind)]

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    # ----------------------------------------------------------------- display
    def links(self) -> Dict[str, List[str]]:
        """Declared states that need a new decision added to their next steps, by state id."""
        links: Dict[str, List[str]] = {}
        for item in self.by_kind(DECISION):
            if item.reached_from.strip():
                links.setdefault(item.reached_from.strip(), []).append(item.id)
        return links

    def merged(self, intake: IntakeData) -> IntakeData:
        """The intake as it would read with these edits applied. For drawing only.

        A copy, deliberately: the caller draws this and the real intake stays exactly as the
        workbook has it, so nothing downstream can accidentally build a benchmark from a sketch.
        """
        decisions = list(intake.decisions) + [
            Decision(id=item.id, name=item.name or item.id, trigger_capability=item.capability,
                     inputs="", variants=list(item.outcomes),
                     input_source=item.input_source or "User")
            for item in self.by_kind(DECISION)]

        # A sketched decision hangs off a state that already exists, so that state is rebuilt with
        # the new step added. State is frozen, which is the point: the copy is for the picture and
        # the declared one is untouched until the commit writes it.
        links = self.links()
        states = []
        for state in intake.states:
            extra = [d for d in links.get(state.id, []) if d not in state.next_decisions]
            states.append(State(id=state.id, reached_via=state.reached_via,
                                description=state.description,
                                next_decisions=list(state.next_decisions) + extra,
                                is_terminal=state.is_terminal, outcome_type=state.outcome_type)
                          if extra else state)

        states += [State(id=item.id, reached_via=item.reached_via,
                         description=item.name or item.id,
                         next_decisions=list(item.next_decisions), is_terminal=item.is_terminal,
                         outcome_type=item.outcome_type)
                   for item in self.by_kind(STATE)]

        return IntakeData(use_case=dict(intake.use_case), personas=list(intake.personas),
                          capabilities=list(intake.capabilities), decisions=decisions,
                          states=states, tools=list(intake.tools))

    def rows(self, intake: IntakeData) -> List[Dict[str, object]]:
        """Each pending item as the page shows it, with whether it has anywhere to go yet."""
        state_ids = {s.id for s in intake.states} | set(self.ids(STATE))
        decision_ids = {d.id for d in intake.decisions} | set(self.ids(DECISION))

        # A sketched decision is attached once something leads to it: a state already declared,
        # a state sketched alongside it, or the attachment recorded against the decision itself.
        leads_to: set = set()
        for state in intake.states:
            leads_to.update(state.next_decisions)
        for item in self.by_kind(STATE):
            leads_to.update(item.next_decisions)

        rows = []
        for item in self.items:
            if item.kind == DECISION:
                source = item.reached_from.strip()
                attached = bool(source) or item.id in leads_to
                where = (f"reached from {source}" if source
                         else "reached from a declared state" if attached
                         else "nothing leads to it yet")
                detail = " / ".join(item.outcomes) or "no outcomes named"
            else:
                attached = bool(item.reached_via.strip())
                where = (f"reached via {item.reached_via}" if attached
                         else "no route to it yet")
                detail = ("ends the interaction" if item.is_terminal
                          else " → ".join(item.next_decisions) or "leads nowhere yet")
            rows.append({"item": item, "kind": item.kind, "id": item.id,
                         "name": item.name or item.id, "detail": detail,
                         "attached": attached, "where": where,
                         "options": state_ids if item.kind == DECISION else decision_ids})
        return rows

    def unattached(self, intake: IntakeData) -> List[str]:
        return [row["id"] for row in self.rows(intake) if not row["attached"]]

    # ----------------------------------------------------------------- committing
    def as_workbook_rows(self) -> Tuple[List[list], List[list]]:
        """The pending items as rows for the intake's own sheets: (decisions, states)."""
        decisions = [[item.id, item.name or item.id, item.capability, "",
                      " / ".join(item.outcomes), item.input_source or "User", 1, ""]
                     for item in self.by_kind(DECISION)]
        states = [[item.id, item.reached_via, item.name or item.id,
                   ", ".join(item.next_decisions), "Y" if item.is_terminal else "N",
                   item.outcome_type]
                  for item in self.by_kind(STATE)]
        return decisions, states
