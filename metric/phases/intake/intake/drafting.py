"""Drafting the intake workbook from the evidence."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from openpyxl import load_workbook

from metric.domain.intake import write_template
from metric.llm import config, council, prompts
from metric.llm.gateway import ask_llm
from metric.shared import parse_json_object
from metric.shared.text import parse_reached_via
from metric.shared.replies import at_least_one, objects as _objects, text as _text

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "ingest.system"
_DRAFT_PROMPT = "intake.draft"
_REVISE_PROMPT = "intake.revise"
_REPAIR_PROMPT = "intake.repair"
_RECONCILE_PROMPT = "intake.reconcile"

CAPABILITY_TYPES = ("Lookup", "Transactional", "Gating", "Advisory", "PII-handling")
INPUT_SOURCES = ("User", "Tool", "Memory-Session", "Memory-CrossSession", "System-Context",
                 "Document")
OUTCOME_TYPES = ("Happy path", "Retry", "Fallback", "Escalation", "Termination")
CONFIDENCE = ("High", "Medium", "Low")

def _cds() -> str:
    """The one definition of capability, decision and state, shared by every prompt that writes a
    graph. Written once because three prompts describing the same three things in slightly
    different words is how two of them end up describing something else."""
    return prompts.load("shared.cds")


def _wiring() -> str:
    """How reached_via and next_decisions connect the graph, shared by every prompt that writes
    one. Kept out of the three prompts themselves because it is the section most often edited: a
    draft whose wiring is wrong is unwalkable however good the prose in it is, and the people who
    tune that instruction should not have to find and match three copies of it."""
    return prompts.load("shared.wiring")


def _structure_block(structure: Optional[dict]) -> str:
    """The diagram-derived graph as a prompt section, or a line saying there was not one."""
    from metric.phases.intake.intake import diagram_structure

    if not structure or diagram_structure.is_empty(structure):
        # No picture, so the graph has to be read out of the prose -- which is the case that used
        # to get *less* help than the one with a diagram, and a warning against going past the
        # evidence on top of it. Prose about an agent describes a graph; drawing that graph is
        # reading, not inventing, and saying so is what stops a documented flow arriving as four
        # disconnected boxes.
        return (
            "No workflow diagram was submitted, so the graph has to be read out of the prose "
            "above. It is in there: documentation describing what an agent does is describing a "
            "flow, in sentences rather than in boxes.\n\n"
            "**Reading it out is not inventing it.** These are readings, and you are expected to "
            "make them:\n\n"
            "- A check the documents describe has ways it can turn out. \"The agent verifies the "
            "cardmember\" is a decision with a pass and a fail, whether or not the failure is "
            "written down -- a verification that cannot fail is not a verification.\n"
            "- \"If ... then ... otherwise ...\" is a decision with two named outcomes, and the "
            "words after *if* are its `outcome_condition`.\n"
            "- A step described as happening after another step is a state between them, even "
            "where the prose runs the two together in one sentence.\n"
            "- Something the documents mention once -- an escalation, a refusal, a hand-off to a "
            "person -- is a real ending, and belongs as a terminal state with an outcome type.\n"
            "- A limit stated anywhere (\"three attempts\", \"within sixty days\") belongs on "
            "the decision it bounds, as `max_attempts` or `outcome_condition`.\n\n"
            "What you may not do is add a step nothing describes: a fraud check nobody mentions, "
            "a confirmation screen no document names. The line is between joining up what is "
            "stated and supplying what is absent. Note in `review_notes` where you joined "
            "something up, so the reader can check that reading rather than hunt for it.")
    return (
        "A workflow diagram was submitted and read into the structure below, box by box. It is "
        "already in the shape you are being asked for, so **start from it**: carry it through, "
        "correct it where the documents above contradict it, and add what it does not cover. It "
        "was read off an image and could not be checked against any text, so it is a draft to "
        "confirm rather than a fact -- but a branch drawn on a diagram and mentioned nowhere in "
        "the prose is still a real branch, and dropping it would leave it untested.\n\n"
        + diagram_structure.render(structure))


_L1_KEYS = [
    ("Use case name", "name"),
    ("Business objective", "objective"),
    ("Agent type", "agent_type"),
    ("Channel / modality", "channel"),
    ("Human handoff triggers", "handoff_triggers"),
    ("Safety requirements", "safety_requirements"),
    ("Success criteria", "success_criteria"),
]


class DraftedIntake:
    """A drafted intake and what the drafter thought of its own work."""

    def __init__(self, data: dict) -> None:
        self.data = data

    @property
    def confidence(self) -> Dict[str, str]:
        raw = self.data.get("confidence") or {}
        return {k: v for k, v in raw.items() if v in CONFIDENCE}

    @property
    def review_notes(self) -> List[dict]:
        return [n for n in (self.data.get("review_notes") or []) if isinstance(n, dict)]

    def counts(self) -> Dict[str, int]:
        return {part: len(self.data.get(part) or [])
                for part in ("personas", "capabilities", "decisions", "states", "tools")}


def draft_intake(context: str, complete: Optional[Callable[..., str]] = None,
                 structure: Optional[dict] = None) -> DraftedIntake:
    """Ask for a filled intake, given everything the documents established."""
    complete = complete or ask_llm
    user = prompts.render(_DRAFT_PROMPT, context=context, cds=_cds(), wiring=_wiring(),
                                structure=_structure_block(structure))
    system = prompts.load(_SYSTEM_PROMPT)

    reply = council.deliberate(complete, system, user, stage="INTAKE_DRAFT",
                               tier=config.stage_tier("INTAKE_DRAFT", config.JUDGEMENT))
    drafted = carry_diagram_through(_validate(parse_json_object(reply)), structure)
    return DraftedIntake(_consolidated(drafted))


def _consolidated(data: dict) -> dict:
    """The declaration with its duplicates folded together, and a review note saying which."""
    from metric.phases.scenario_generator.review.consolidate import consolidate

    folded, notes = consolidate(data)
    if notes:
        folded["review_notes"] = list(folded.get("review_notes") or []) + [
            {"field": "Folded together", "note": note} for note in notes]
    return folded


# How each part of a declaration is identified, for matching a repair's rows against the draft's.
_ROW_KEY = {"personas": "id", "capabilities": "id", "decisions": "id", "states": "id",
            "tools": "name"}


def carry_diagram_through(data: dict, structure: Optional[dict]) -> dict:
    """The draft, with every box the diagram declared and the draft dropped put back."""
    from metric.phases.intake.intake import diagram_structure

    if not structure or diagram_structure.is_empty(structure):
        return data

    merged = dict(data)
    # Through the same validation the drafted rows went through, so a carried row cannot arrive
    # with a capability type or outcome type the rest of the tool does not recognise.
    drawn = _validate({part: list(structure.get(part) or [])
                       for part in ("capabilities", "decisions", "states")})

    superseded = _states_taken_over(drawn, data)
    restored: Dict[str, List[str]] = {}
    for part in ("decisions", "states"):
        kept = list(data.get(part) or [])
        seen = {str(row.get("id", "")).strip() for row in kept}
        added = [row for row in drawn[part]
                 if str(row.get("id", "")).strip() not in seen
                 and not (part == "states" and str(row.get("id", "")).strip() in superseded)]
        if added:
            restored[part] = [str(row["id"]) for row in added]
        merged[part] = kept + added

    # Outcomes are the branch labels, so a decision can survive with a route missing from it.
    drew = {d["id"]: [str(o) for o in d["outcomes"]] for d in drawn["decisions"]}
    for decision in merged["decisions"]:
        outcomes = [str(o) for o in (decision.get("outcomes") or [])]
        lowered = {o.lower() for o in outcomes}
        dropped = [o for o in drew.get(str(decision.get("id", "")), [])
                   if o.lower() not in lowered]
        if dropped:
            decision["outcomes"] = outcomes + dropped
            restored.setdefault("outcomes", []).extend(
                f"{decision.get('id', '')}={o}" for o in dropped)

    # A capability only where something now points at it. Carrying the rest would put rows in the
    # workbook that no decision names, which is noise in the one sheet a reviewer reads first.
    named = {str(d.get("capability_id", "")).strip() for d in merged["decisions"]}
    have = {str(c.get("id", "")).strip() for c in (data.get("capabilities") or [])}
    missing = [c for c in drawn["capabilities"] if c["id"] in named and c["id"] not in have]
    if missing:
        restored["capabilities"] = [c["id"] for c in missing]
    merged["capabilities"] = list(data.get("capabilities") or []) + missing

    if restored:
        detail = "; ".join(f"{part}: {', '.join(ids)}" for part, ids in sorted(restored.items()))
        logger.warning("The draft left out %s, all of which were read off a submitted workflow "
                       "diagram. They were carried into the declaration unchanged.", detail)
        merged["review_notes"] = list(data.get("review_notes") or []) + [{
            "field": "Read from the diagram",
            "note": ("Taken straight from the submitted workflow diagram because the draft did "
                     f"not include them — {detail}. They were read off an image and checked "
                     "against no text, so confirm them before relying on them.")}]
    return merged


def carry_forward(first: dict, repaired: dict) -> dict:
    """The repair, with anything it silently dropped put back. Returns a new declaration."""
    merged = dict(repaired)
    superseded = _states_taken_over(first, repaired)
    for part, key in _ROW_KEY.items():
        kept = list(repaired.get(part) or [])
        seen = {str(row.get(key, "")).strip() for row in kept}
        for row in (first.get(part) or []):
            identifier = str(row.get(key, "")).strip()
            if identifier in seen or (part == "states" and identifier in superseded):
                continue
            kept.append(row)
        merged[part] = kept

    # Outcomes are the branch labels, so losing one loses a route even when the decision survives.
    was = {str(d.get("id", "")): [str(o) for o in (d.get("outcomes") or [])]
           for d in (first.get("decisions") or [])}
    for decision in merged["decisions"]:
        outcomes = [str(o) for o in (decision.get("outcomes") or [])]
        dropped = [o for o in was.get(str(decision.get("id", "")), []) if o not in outcomes]
        if dropped:
            decision["outcomes"] = outcomes + dropped
    return merged


def _states_taken_over(first: dict, repaired: dict) -> Set[str]:
    """States the repair dropped because another state now claims every route into them."""
    claimed: Set[str] = set()
    for state in (repaired.get("states") or []):
        claimed.update(parse_reached_via(str(state.get("reached_via", ""))))

    kept = {str(state.get("id", "")).strip() for state in (repaired.get("states") or [])}
    taken_over = set()
    for state in (first.get("states") or []):
        identifier = str(state.get("id", "")).strip()
        edges = set(parse_reached_via(str(state.get("reached_via", ""))))
        if identifier and identifier not in kept and edges and edges <= claimed:
            taken_over.add(identifier)
    return taken_over


def repair_intake(context: str, current: str, problems: List[str],
                  complete: Optional[Callable[..., str]] = None,
                  structure: Optional[dict] = None,
                  enumeration: str = "") -> Optional[DraftedIntake]:
    """Put the draft's own structural failures back to the model, with the documents still in hand."""
    if not problems:
        return None

    complete = complete or ask_llm
    user = prompts.render(_REPAIR_PROMPT, context=context, current=current,
                                cds=_cds(), wiring=_wiring(),
                                structure=_structure_block(structure),
                                enumeration=enumeration or "Not available.",
                                problems="\n".join(f"- {problem}" for problem in problems))
    system = prompts.load(_SYSTEM_PROMPT)

    try:
        reply = council.deliberate(complete, system, user, stage="INTAKE_REPAIR",
                                   tier=config.stage_tier("INTAKE_REPAIR", config.JUDGEMENT))
        repaired = DraftedIntake(_validate(parse_json_object(reply)))
    except Exception as exc:
        logger.warning("Could not fill in what the draft left out: %s", exc)
        return None

    # A repair that empties the declaration is not a repair. The check is deliberately crude --
    # it is guarding against a reply that parsed but said nothing, not judging the content.
    if not repaired.data["decisions"] or not repaired.data["states"]:
        logger.warning("The repair pass returned an empty graph; keeping the first draft.")
        return None
    return repaired


def reconcile_intake(context: str, current: str,
                     complete: Optional[Callable[..., str]] = None,
                     structure: Optional[dict] = None,
                     enumeration: str = "") -> Optional[DraftedIntake]:
    """Read the finished declaration whole, once, and correct what only reading it whole reveals."""
    complete = complete or ask_llm
    user = prompts.render(_RECONCILE_PROMPT, context=context, current=current,
                                cds=_cds(), wiring=_wiring(),
                                structure=_structure_block(structure),
                                enumeration=enumeration or "Not available.")
    system = prompts.load(_SYSTEM_PROMPT)

    try:
        reply = council.deliberate(complete, system, user, stage="INTAKE_RECONCILE",
                                   tier=config.stage_tier("INTAKE_RECONCILE", config.JUDGEMENT))
        reconciled = DraftedIntake(_validate(parse_json_object(reply)))
    except Exception as exc:
        logger.warning("Could not reconcile the declaration: %s", exc)
        return None

    if not reconciled.data["decisions"] or not reconciled.data["states"]:
        logger.warning("The reconciliation returned an empty graph; keeping what was there.")
        return None
    return reconciled


def revise_intake(context: str, current: str, complete: Optional[Callable[..., str]] = None,
                  structure: Optional[dict] = None) -> DraftedIntake:
    """Ask for the intake revised in place, given what has been added since it was last written."""
    complete = complete or ask_llm
    user = prompts.render(_REVISE_PROMPT, context=context, current=current,
                                cds=_cds(), wiring=_wiring(),
                                structure=_structure_block(structure))
    system = prompts.load(_SYSTEM_PROMPT)

    try:
        reply = complete(system, user, tier=config.stage_tier("INTAKE_DRAFT", config.JUDGEMENT))
    except TypeError:                                      # a stub completion without the keywords
        reply = complete(system, user)

    return DraftedIntake(_consolidated(_validate(parse_json_object(reply))))


# A persona is a person arriving with an objective, and only two objectives are universal: to use
# the service as intended, and to make it do something it should not. Anything past those has to
# earn its place by changing what the agent does.
MAX_PERSONAS = 4

COOPERATIVE = {"id": "P1", "name": "Cooperative user",
               "applies_to": "Wants the service to work and is honestly trying to use it",
               "is_default": True}
ADVERSARIAL = {"id": "P-ADV", "name": "Adversarial user",
               "applies_to": "Trying to make the agent act outside its remit",
               "is_default": False}

# Words that describe how someone behaves rather than what they came to do. A persona named only
# by one of these is the same objective in a different tone, and enumerating tones tests the same
# route repeatedly while the routes that matter go untested.
_MANNER_ONLY = ("impatient", "confused", "frustrated", "angry", "polite", "rude", "verbose",
                "terse", "hurried", "novice", "expert", "first-time", "returning", "elderly",
                "young", "casual", "formal", "chatty", "brief")


def _is_adversarial(persona: dict) -> bool:
    text = f"{persona['name']} {persona['applies_to']}".lower()
    return any(word in text for word in
               ("adversar", "malicious", "attacker", "abus", "hostile", "bad actor",
                "non-cooperative", "noncooperative", "fraud"))


def _earns_its_place(persona: dict) -> bool:
    """Whether an extra persona describes a different objective rather than a different manner."""
    difference = persona["applies_to"].strip()
    if len(difference) < 12:
        return False
    name = persona["name"].strip().lower()
    return not any(name.startswith(word) or name == word for word in _MANNER_ONLY)


def _personas(data: dict) -> List[dict]:
    """The people who arrive, as objectives rather than temperaments."""
    drafted = []
    for entry in _objects(data, "personas"):
        identifier = _text(entry, "id") or f"P{len(drafted) + 1}"
        drafted.append({"id": identifier, "name": _text(entry, "name") or identifier,
                        "applies_to": _text(entry, "applies_to"),
                        "is_default": bool(entry.get("is_default"))})

    adversarial = [p for p in drafted if _is_adversarial(p)]
    others = [p for p in drafted if not _is_adversarial(p)]

    cooperative = next((p for p in others if p["is_default"]), None) or (
        others[0] if others else dict(COOPERATIVE))
    cooperative["is_default"] = True

    extras = [p for p in others if p is not cooperative and _earns_its_place(p)]
    kept = [cooperative, adversarial[0] if adversarial else dict(ADVERSARIAL)]
    for persona in extras[:max(0, MAX_PERSONAS - len(kept))]:
        persona["is_default"] = False
        kept.append(persona)

    dropped = len(drafted) - len([p for p in kept if p in drafted])
    if dropped > 0:
        logger.info("Kept %d persona(s) of %d drafted. The rest described how someone speaks "
                    "rather than what they came to do, which is not a persona.",
                    len(kept), len(drafted))
    return kept


def _validate(data: dict) -> dict:
    """Keep the parts that fit the intake's vocabulary and drop what does not."""
    use_case = data.get("use_case") if isinstance(data.get("use_case"), dict) else {}

    personas = _personas(data)

    capabilities = []
    for entry in _objects(data, "capabilities"):
        kind = _text(entry, "type")
        capabilities.append({"id": _text(entry, "id"), "name": _text(entry, "name"),
                             "type": kind if kind in CAPABILITY_TYPES else ""})

    decisions = []
    for entry in _objects(data, "decisions"):
        source = _text(entry, "input_source")
        outcomes = [str(o).strip() for o in (entry.get("outcomes") or []) if str(o).strip()]
        decisions.append({
            "id": _text(entry, "id"), "name": _text(entry, "name"),
            "capability_id": _text(entry, "capability_id"), "inputs": _text(entry, "inputs"),
            "outcomes": outcomes,
            "input_source": source if source in INPUT_SOURCES else "User",
            "max_attempts": at_least_one(entry.get("max_attempts")),
            "outcome_condition": _text(entry, "outcome_condition"),
        })

    states = []
    for entry in _objects(data, "states"):
        terminal = bool(entry.get("is_terminal"))
        outcome_type = _text(entry, "outcome_type")
        states.append({
            "id": _text(entry, "id"), "reached_via": _text(entry, "reached_via"),
            "description": _text(entry, "description"),
            "next_decisions": [str(d).strip() for d in (entry.get("next_decisions") or [])
                               if str(d).strip()],
            "is_terminal": terminal,
            "outcome_type": outcome_type if terminal and outcome_type in OUTCOME_TYPES else "",
        })

    tools = [{"name": _text(e, "name"), "capability_id": _text(e, "capability_id"),
              "changes_state": bool(e.get("changes_state"))}
             for e in _objects(data, "tools") if _text(e, "name")]

    return {"use_case": use_case, "personas": personas, "capabilities": capabilities,
            "decisions": decisions, "states": states, "tools": tools,
            "confidence": data.get("confidence") or {},
            "review_notes": data.get("review_notes") or []}


def _spans_already_drawn(path: Path) -> Dict[str, Tuple[str, str]]:
    """Each capability's entry and exit cells, read off the workbook about to be overwritten."""
    if not path.exists():
        return {}
    try:
        book = load_workbook(path)
        if "L2 Capabilities" not in book.sheetnames:
            return {}
        sheet = book["L2 Capabilities"]
        drawn = {}
        for row in sheet.iter_rows(min_row=2):
            identifier = str(row[0].value or "").strip() if row else ""
            if not identifier:
                continue
            entry = str(row[3].value or "").strip() if len(row) > 3 else ""
            exit_states = str(row[4].value or "").strip() if len(row) > 4 else ""
            if entry or exit_states:
                drawn[identifier] = (entry, exit_states)
        return drawn
    except Exception as exc:                     # an unreadable prior file is not a reason to fail
        logger.warning("Could not read the existing capability spans back: %s", exc)
        return {}


def write_drafted_intake(path: Path, draft: DraftedIntake) -> None:
    """Write the draft into a workbook of exactly the shape ``init-template`` produces."""
    path = Path(path)
    drawn = _spans_already_drawn(path)
    write_template(str(path))
    book = load_workbook(path)
    data = draft.data

    # Written by looking each label up in the sheet, not by counting rows. The template carries
    # fields the drafter is not asked for -- a rating, known limitations -- so the two lists are
    # different lengths and in different orders, and walking them in step lands every field after
    # the second in the wrong row. Worse, a positional write guarded by a label check does not
    # land in the wrong row: it lands nowhere, and five of the seven fields are dropped in silence.
    use_case = data.get("use_case") or {}
    sheet = book["L1 Use Case"]
    at = {str(sheet.cell(row=row, column=1).value or "").strip(): row
          for row in range(2, sheet.max_row + 1)}
    for label, key in _L1_KEYS:
        row = at.get(label)
        if row is None:
            logger.warning("The intake template has no '%s' row; that field was not written.",
                           label)
            continue
        sheet.cell(row=row, column=2, value=str(use_case.get(key, "") or ""))

    for persona in data["personas"]:
        book["Personas"].append([persona["id"], persona["name"], persona["applies_to"],
                                 "Y" if persona["is_default"] else ""])

    for capability in data["capabilities"]:
        entry, exit_states = drawn.get(capability["id"], ("", ""))
        book["L2 Capabilities"].append(
            [capability["id"], capability["name"], capability["type"], entry, exit_states])

    for decision in data["decisions"]:
        book["L3 Decisions"].append([
            decision["id"], decision["name"], decision["capability_id"], decision["inputs"],
            " / ".join(decision["outcomes"]), decision["input_source"],
            decision["max_attempts"], decision["outcome_condition"],
            # Never drafted. Whether a decision is already covered by a separate engagement is a
            # scoping call the validator makes, not something the documents state.
            "No"])

    for state in data["states"]:
        book["L4 States"].append([
            state["id"], state["reached_via"], state["description"],
            ", ".join(state["next_decisions"]),
            "Yes" if state["is_terminal"] else "No", state["outcome_type"]])

    for tool in data["tools"]:
        book["Tools"].append([tool["name"], tool["capability_id"],
                              "Yes" if tool["changes_state"] else "No"])

    _write_review_sheet(book, draft)
    book.save(path)


def _write_review_sheet(book, draft: DraftedIntake) -> None:
    """Where the draft says what it is unsure of, so a reviewer knows where to look first."""
    sheet = book.create_sheet("Review This")
    sheet.append(["Part", "Confidence", "What to check"])
    for column, width in zip("ABC", (22, 14, 110)):
        sheet.column_dimensions[column].width = width

    confidence = draft.confidence
    for part in ("use_case", "personas", "capabilities", "decisions", "states", "tools"):
        sheet.append([part.replace("_", " ").title(), confidence.get(part, "Low"), ""])

    sheet.append([])
    sheet.append(["Note", "", ""])
    for note in draft.review_notes:
        sheet.append([str(note.get("field", ""))[:60], "",
                      str(note.get("note", ""))[:500]])

    if not draft.review_notes:
        sheet.append(["", "", "The draft raised nothing specific. Read it against the source "
                              "documents regardless — it is a draft, not a finding."])
