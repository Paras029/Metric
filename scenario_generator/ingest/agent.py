"""Reading a submitted pack and filling the intake, as one loop.

This is the ingestion path, not a pass that runs after one. The sequence it replaces read every
document against three groups of questions, put what was left open back to them twice more,
drafted a declaration from the result and repaired it once -- seven to nine model calls in a fixed
order, every one of them made whether or not it had anything to do. Running a loop *after* that
spent them twice over, which is the reason this replaces the sequence rather than following it.

What the sequence did well is kept, and most of it was never a model call to begin with. Files are
still parsed and redacted deterministically before anything sees them, so no passage reaches a
model that has not been through redaction. Diagrams are still read by the same tested vision pass,
one call per image. The declaration is still validated against the intake's own vocabulary,
consolidated, and written into a workbook of exactly the shape a person would have filled in.

What changes is what decides the order and the number. The loop reads what it judges worth
reading, writes a declaration, audits it, and goes back for what is missing -- so a two-page pack
costs a fraction of a sixty-page one instead of the same fixed seven, and a declaration that is
still incomplete after the first write gets another look instead of being handed on regardless.

Three things make that safe to point at a model.

**The termination oracle is deterministic.** Whether the declaration is finished is decided by
:func:`core.gaps.find_gaps` and the graph audit, never by the model saying it is done. A loop that
judges its own completion runs until it feels like stopping, which on a bad day is never and on a
worse day is immediately.

**Every tool is an existing, tested reader.** Nothing here parses a document or reads an image; it
calls the code that already does, so the loop cannot be a second way of doing the same job that
drifts from the first.

**The budget is hard and the work is idempotent.** The declaration on disk is the state, so a run
stopped at any point -- budget spent, gateway down, stage cancelled -- leaves a workbook exactly as
good as the last thing written to it. There is no half-applied turn.

Capability spans are the one thing the loop may not write. Where a block of the agent begins and
ends is a judgement a person makes against the drawing, and it decides how the entire scenario
space is enumerated; the loop fills in everything *else* about a capability -- its type, and what
it does, from the decisions inside the span and the documents that describe them -- and carries
the span across untouched.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..core.gaps import find_gaps
from ..core.graph import DecisionGraph
from ..core.intake import read_intake
from ..llm import config, prompt_loader

logger = logging.getLogger(__name__)

# What a full run is allowed to spend. A backstop against a loop that will not settle, not a
# budget it aims at: an ordinary pack finishes in three or four turns because the loop stops when
# the declaration audits clean, and the sequence this replaces spent seven to nine every time
# whatever the pack contained.
MAX_TURNS = 24

# How many sweeps run before the loop stops and asks. Two, because the first reads and the second
# fixes what the first left; a third mostly re-litigates the second, and every turn past the point
# of convergence is a call spent restating a declaration that was already written.
SWEEPS_BEFORE_PAUSE = 2

_SYSTEM_PROMPT = "ingest.agent"


@dataclass
class Progress:
    """What the loop has done and what is left, as figures rather than as narration."""

    turns: int = 0
    calls: int = 0
    tools_run: List[str] = field(default_factory=list)
    gaps_at_start: int = 0
    gaps_now: int = 0
    questions: List[str] = field(default_factory=list)
    findings: Dict[str, List[str]] = field(default_factory=dict)
    stopped_because: str = ""

    def summary(self) -> Dict[str, object]:
        closed = self.gaps_at_start - self.gaps_now
        return {
            "Turns": self.turns,
            "Model calls": self.calls,
            "Gaps at the start": self.gaps_at_start,
            "Gaps now": self.gaps_now,
            "Closed by the loop": closed if closed > 0 else 0,
            "Questions for the model owner": len(self.questions),
            "Stopped because": self.stopped_because,
        }


def outstanding(intake_path: str) -> List[str]:
    """Everything structurally wrong with the declaration on disk, as sentences.

    The loop's termination oracle, and the filter every question it asks has to pass. Both read
    the *declaration* rather than the documents, so what comes back names a row and a hole --
    "DEC-03's outcome 'Timeout' leads to no declared state" -- rather than describing a topic the
    documents covered thinly.
    """
    try:
        intake = read_intake(intake_path)
    except Exception as exc:
        return [f"The declaration could not be read: {exc}"]

    # Labelled by the row it concerns, falling back to the kind of row where there is no id --
    # a use-case field belongs to the use case rather than to a row, and printing it as ": What
    # kind of agent is this?" gives a model nothing to name back and gives the question filter
    # nothing to match on.
    problems = [f"{gap.target_id or gap.kind}: {gap.question}" for gap in find_gaps(intake)]
    graph = DecisionGraph(intake.decisions, intake.states)
    for decision in intake.decisions:
        if decision.out_of_scope:
            continue
        for variant in decision.variants:
            if graph.successor(decision.id, variant).startswith("OUT:"):
                problems.append(
                    f"{decision.id}: outcome \"{variant}\" leads to no declared state, so any "
                    f"route taking it stops there and becomes no scenario.")
    return problems


def is_answerable_question(question: str, problems: Sequence[str]) -> bool:
    """Whether a question the model wants to ask is one a person can actually act on.

    Filtered in code rather than asked for in the prompt, because the failure is not that the
    model asks too many questions -- it is that it asks unanswerable ones. "Is decision seven
    clear?" cannot be answered: the person does not know what would make it clear, and a list of
    forty such questions is a list nobody opens. A question earns its place by naming a row the
    audit is already complaining about, which is the same test the loop uses to decide it is done.
    """
    text = str(question or "").strip()
    if len(text) < 15:
        return False
    named = {word.strip(".,:;\"'") for word in text.replace("(", " ").replace(")", " ").split()}
    return any(any(token in named for token in problem.split(":")[0].split())
               for problem in problems)


@dataclass
class Tool:
    """One thing the loop can do, as a name, a description and a callable.

    Held as data rather than as LangChain tool objects so the loop can be driven and tested
    without a gateway. :func:`bind` turns them into whatever the model needs at the point of use.
    """

    name: str
    description: str
    run: Callable[..., str]


class Pack:
    """Everything submitted, parsed and redacted once, before any model sees it.

    Deterministic and done up front, because it is not a model call and never was: parsing a PDF,
    masking what redaction masks, and setting images aside for the vision pass cost nothing but
    wall-clock, and doing them once means no tool call can reach a passage redaction has not been
    through. Parsing is lazy per file -- a sixty-page appendix nobody opens is never parsed --
    but redaction is not optional on anything that is.
    """

    def __init__(self, root: Path, should_redact=None) -> None:
        from .groups import evidence_files
        from .readers import is_image

        self.root = Path(root)
        self._should_redact = should_redact or (lambda path: False)
        self.files: Dict[str, Path] = {
            path.name: path
            for paths in evidence_files(self.root).values() for path in paths}
        self.documents = {n: p for n, p in self.files.items() if not is_image(p)}
        self.diagrams = {n: p for n, p in self.files.items() if is_image(p)}
        self._text: Dict[str, str] = {}
        self._mapping = None

    def text_of(self, name: str) -> str:
        """One document as locatable, redacted text. Parsed once and remembered.

        The redaction mapping carries between documents, which is why they are masked here in one
        place rather than per call: a substitution engine has to mask the same name the same way
        everywhere it appears, and a per-call mask would give one person three different aliases
        across three documents and make the pack unreadable as a whole.
        """
        from .readers import UnreadableDocument, read_document
        from .redaction import redact_segments

        if name in self._text:
            return self._text[name]
        path = self.documents[name]
        try:
            _, segments = read_document(path)
        except UnreadableDocument as exc:
            self._text[name] = f"{name} could not be read: {exc}"
            return self._text[name]
        segments, self._mapping = redact_segments(
            segments, self._mapping, force=self._should_redact(path))
        self._text[name] = "\n\n".join(f"[{s.locator}]\n{s.text}" for s in segments)
        return self._text[name]


def build_tools(pack: "Pack", intake_path: str, findings: Dict[str, List[str]],
                structure: Dict[str, list], describe_images=None) -> List[Tool]:
    """The loop's whole surface: read what was submitted, write what it establishes, check itself.

    Every one wraps a reader or writer that already exists and is already tested. Nothing here
    parses a document, reads an image, or validates a declaration on its own account -- a second
    implementation of any of those would be a second thing to keep in step with the first, and the
    first is the one that has been through redaction, grounding and the intake's own vocabulary.
    """
    from .readers import is_image

    def list_sources(**_) -> str:
        if not pack.files:
            return ("Nothing was submitted. Build the declaration from what is already in the "
                    "workbook, or record questions for the model owner.")
        return json.dumps([{"name": name, "kind": "diagram" if is_image(path) else "document"}
                           for name, path in sorted(pack.files.items())], indent=2)

    def read_one_document(name: str = "", **_) -> str:
        key = str(name).strip()
        if key in pack.diagrams:
            return f"{key} is an image; call read_diagram for it."
        if key not in pack.documents:
            return f"There is no submitted document called {key!r}. Call list_sources first."
        return pack.text_of(key)

    def read_one_diagram(name: str = "", **_) -> str:
        """One image, read into the intake's own vocabulary by the existing vision pass.

        A nested model call, and the only one in the tool surface. It stays nested because a
        workflow diagram *is* the decision and state sheets, and the pass that reads one that way
        is audited against its own picture -- handing the raw image into this conversation instead
        would be a second, unaudited way of reading the same thing.
        """
        from . import diagram_structure
        from .extraction import DocumentExtractor

        key = str(name).strip()
        if key not in pack.diagrams:
            return f"There is no submitted diagram called {key!r}. Call list_sources first."
        try:
            reader = DocumentExtractor(describe_images=describe_images)
            record = _empty_record()
            reader._read_diagrams([pack.diagrams[key]], record)
        except Exception as exc:
            return f"{key} could not be read: {exc}"
        read = record.structure or {}
        if diagram_structure.is_empty(read):
            return f"Nothing could be read off {key}."
        for part, rows in read.items():
            structure.setdefault(part, [])
            have = {r.get("id") for r in structure[part]}
            structure[part] += [r for r in rows if r.get("id") not in have]
        return diagram_structure.render(read)

    def what_is_declared(**_) -> str:
        from ..llm.context import describe_graph, describe_use_case
        try:
            intake = read_intake(intake_path)
        except Exception as exc:
            return f"The declaration could not be read: {exc}"
        return f"{describe_use_case(intake)}\n\n{describe_graph(intake)}"

    def audit(**_) -> str:
        problems = outstanding(intake_path)
        if not problems:
            return ("The declaration is structurally complete: every outcome leads somewhere, "
                    "every state is reached, and nothing required is blank.")
        return "\n".join(f"- {problem}" for problem in problems)

    def write(declaration: str = "", **_) -> str:
        return _write_declaration(declaration, intake_path, structure)

    def record(facet: str = "", statement: str = "", **_) -> str:
        """What a document establishes, filed against the question it answers.

        Kept because every stage after this one is grounded on it rather than on the workbook: the
        writer needs to know what the agent is *for* to describe a route through it, and the graph
        alone does not say. Deterministic -- this only files what it is given.
        """
        from ..core.evidence import FACETS

        key = str(facet).strip().lower()
        if key not in FACETS:
            return (f"{facet!r} is not one of the questions this records against. Use one of: "
                    + ", ".join(FACETS))
        text = str(statement).strip()
        if len(text) < 10:
            return "Nothing recorded: say what the documents establish, in a sentence."
        findings.setdefault(key, [])
        if text not in findings[key]:
            findings[key].append(text)
        return "Recorded."

    return [
        Tool("list_sources", "List every submitted file, and whether each is a document or a "
                             "workflow diagram. Call this first.", list_sources),
        Tool("read_document", "Read one submitted document in full, with its page markers. "
                              "Takes the file name exactly as list_sources gave it.",
             read_one_document),
        Tool("read_diagram", "Read one submitted workflow diagram into decisions, outcomes and "
                             "states. Takes the file name exactly as list_sources gave it.",
             read_one_diagram),
        Tool("what_is_declared", "The declaration as it currently stands: the use case, and the "
                                 "decision graph with every edge.", what_is_declared),
        Tool("audit_declaration", "What is still structurally wrong with the declaration. This "
                                  "is what decides whether the work is finished, so check it "
                                  "before concluding anything is.", audit),
        Tool("write_declaration", "Write the declaration. Takes a JSON object with use_case, "
                                  "personas, capabilities, decisions, states and tools. Replaces "
                                  "what is there, so send the whole declaration every time, not "
                                  "a patch.", write),
        Tool("record_finding", "File what the documents establish about one of the questions "
                               "every later stage is grounded on. Takes a facet and a sentence.",
             record),
    ]


def _empty_record():
    from ..core.evidence import EvidenceRecord
    return EvidenceRecord()


def _write_declaration(declaration: str, intake_path: str, structure: Dict[str, list]) -> str:
    """Validate a proposed declaration and write it, or say exactly why it was not written.

    The one tool that changes anything, so it is the one that refuses. A reply that does not parse,
    or that declares no graph at all, is rejected with the reason rather than written -- an empty
    workbook overwriting a partial one is the single worst thing a loop could do with a turn, and
    it is also the easiest for a model to produce by accident.
    """
    from ..utils import parse_json_object
    from .drafting import DraftedIntake, _consolidated, _validate, carry_diagram_through
    from .drafting import write_drafted_intake

    try:
        proposed = parse_json_object(declaration)
    except Exception as exc:
        return f"Not written: that is not a JSON object ({exc}). Send the whole declaration."
    if not isinstance(proposed, dict):
        return "Not written: send a JSON object with use_case, decisions and states."

    data = _consolidated(carry_diagram_through(_validate(proposed), structure or None))
    if not data["decisions"] or not data["states"]:
        return ("Not written: a declaration with no decisions or no states describes no agent. "
                "Read the documents and send the graph.")

    write_drafted_intake(Path(intake_path), DraftedIntake(data))
    remaining = outstanding(intake_path)
    if not remaining:
        return ("Written, and it audits clean: every outcome leads somewhere and nothing "
                "required is blank.")
    return ("Written. Still outstanding:\n"
            + "\n".join(f"- {problem}" for problem in remaining))


def build_question_tool(problems: Sequence[str], recorded: List[str]) -> Tool:
    """A tool for the one thing the loop cannot do for itself: ask a person.

    Filtered on the way in. The model is not asked to restrain itself -- it is allowed to try, and
    a question that names nothing the audit is complaining about is refused with the reason, which
    is both cheaper and more reliable than a paragraph of prompt about what makes a good question.
    """
    def ask(question: str = "", **_) -> str:
        text = str(question or "").strip()
        if not is_answerable_question(text, problems):
            return ("Not recorded. That question does not name a row the audit is complaining "
                    "about, so nobody can act on it. Ask about a specific decision, state, "
                    "capability or tool that audit_declaration named, or settle it from the "
                    "documents instead.")
        if text in recorded:
            return "Already recorded."
        recorded.append(text)
        return "Recorded for the model owner."

    return Tool("ask_the_model_owner",
                "Record one question for the person who submitted the pack. Only for something "
                "the documents genuinely do not settle, naming a specific row the audit is "
                "complaining about.", ask)


class Conversation:
    """One exchange with a tool-calling model, in a shape the loop can be tested without one.

    The loop deals in plain dictionaries -- ``{"tool": name, "args": {...}, "id": ...}`` -- and
    this adapts them to whatever the gateway hands back. Keeping the loop free of LangChain
    message classes is what lets every one of its decisions be driven by a stub, and the decisions
    are the part worth testing: a loop tested only against a live model is tested on a good day.
    """

    def __init__(self, tools: Sequence[Tool], tier=None) -> None:
        self._tools = list(tools)
        self._tier = tier or config.JUDGEMENT
        self._model = None

    def _bound(self):
        if self._model is None:
            from langchain_core.tools import StructuredTool

            from ..llm.gateway import tool_model
            self._model = tool_model(
                [StructuredTool.from_function(func=tool.run, name=tool.name,
                                              description=tool.description)
                 for tool in self._tools], tier=self._tier)
        return self._model

    def __call__(self, messages: List[dict]) -> dict:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        kinds = {"system": SystemMessage, "human": HumanMessage}
        history = []
        for message in messages:
            role = message["role"]
            if role == "tool":
                history.append(ToolMessage(content=message["content"],
                                           tool_call_id=message["id"]))
            elif role == "assistant":
                history.append(AIMessage(content=message.get("content", ""),
                                         tool_calls=message.get("raw_tool_calls", [])))
            else:
                history.append(kinds[role](message["content"]))

        reply = self._bound().invoke(history)
        calls = [{"tool": call["name"], "args": call.get("args") or {}, "id": call["id"]}
                 for call in (getattr(reply, "tool_calls", None) or [])]
        return {"content": reply.content or "", "tool_calls": calls,
                "raw_tool_calls": getattr(reply, "tool_calls", None) or []}


def run(workspace_root: Path, intake_path: str, converse=None, progress=None,
        max_turns: int = MAX_TURNS, sweeps: int = SWEEPS_BEFORE_PAUSE,
        should_redact=None, describe_images=None, notes: Sequence[str] = ()) -> Progress:
    """Read the pack and fill the declaration, and stop when it is complete or the budget is spent.

    Returns what happened rather than the declaration: the declaration is the workbook at
    ``intake_path``, which every tool reads and the caller already has. Nothing is left
    half-applied -- a run stopped at any point leaves the workbook exactly as good as the last
    thing written to it.
    """
    report = progress or (lambda *args, **kwargs: None)
    pack = Pack(Path(workspace_root), should_redact=should_redact)
    findings: Dict[str, List[str]] = {}
    structure: Dict[str, list] = {}
    questions: List[str] = []

    problems = outstanding(intake_path)
    state = Progress(gaps_at_start=len(problems), gaps_now=len(problems))
    started_from_nothing = not Path(intake_path).exists()
    if not problems and not started_from_nothing:
        state.stopped_because = "the declaration was already structurally complete"
        return _finish(state, intake_path, questions, findings, structure, workspace_root)

    tools = build_tools(pack, intake_path, findings, structure, describe_images=describe_images)
    tools.append(build_question_tool(problems, questions))
    by_name = {tool.name: tool for tool in tools}
    converse = converse or Conversation(tools)

    messages: List[dict] = [
        {"role": "system", "content": prompt_loader.load(_SYSTEM_PROMPT)},
        {"role": "human", "content": _opening(pack, problems, started_from_nothing, notes)},
    ]

    for sweep in range(1, sweeps + 1):
        while state.turns < max_turns:
            state.turns += 1
            report(_LINE, state.turns, max_turns)
            try:
                reply = converse(messages)
            except Exception as exc:
                logger.warning("The loop's model call failed on turn %d: %s", state.turns, exc)
                state.stopped_because = f"a model call failed: {exc}"
                return _finish(state, intake_path, questions, findings, structure, workspace_root)
            state.calls += 1

            if not reply.get("tool_calls"):
                break

            messages.append({"role": "assistant", "content": reply.get("content", ""),
                             "raw_tool_calls": reply.get("raw_tool_calls", [])})
            for call in reply["tool_calls"]:
                tool = by_name.get(call["tool"])
                state.tools_run.append(call["tool"])
                if tool is None:
                    result = f"There is no tool called {call['tool']!r}."
                else:
                    try:
                        result = tool.run(**(call.get("args") or {}))
                    except Exception as exc:            # a tool failing is information, not a stop
                        result = f"{call['tool']} failed: {exc}"
                messages.append({"role": "tool", "content": str(result), "id": call["id"]})

        remaining = outstanding(intake_path)
        state.gaps_now = len(remaining)
        if not remaining and Path(intake_path).exists():
            state.stopped_because = "the declaration audits clean"
            return _finish(state, intake_path, questions, findings, structure, workspace_root)
        if state.turns >= max_turns:
            state.stopped_because = f"the budget of {max_turns} turns was spent"
            return _finish(state, intake_path, questions, findings, structure, workspace_root)

        problems = remaining
        messages.append({"role": "human", "content":
                         "Still outstanding:\n\n"
                         + "\n".join(f"- {problem}" for problem in remaining)
                         + "\n\nSettle what the documents settle. Record a question for the "
                           "model owner only where they genuinely do not."})

    state.stopped_because = (f"{sweeps} sweeps finished with {state.gaps_now} left, which the "
                             f"documents do not appear to settle")
    return _finish(state, intake_path, questions, findings, structure, workspace_root)


_LINE = "Reading the pack and filling the declaration"


def _opening(pack: "Pack", problems: Sequence[str], from_nothing: bool,
             notes: Sequence[str]) -> str:
    """The first message: what was submitted, what exists, and what is wrong with it.

    Named files rather than a count, because the first thing the loop has to decide is what to
    open, and a list it already has is a tool call it does not have to spend.
    """
    submitted = ("Submitted: "
                 + ", ".join(sorted(pack.files)) if pack.files else
                 "Nothing was submitted.")
    if from_nothing:
        state = ("There is no declaration yet. Read what was submitted and write one with "
                 "write_declaration.")
    else:
        state = ("A declaration exists. What is wrong with it:\n\n"
                 + "\n".join(f"- {problem}" for problem in problems)
                 + "\n\nRead whatever bears on those and settle as many as the documents allow.")
    said = ("\n\nThe validator has also said:\n"
            + "\n".join(f"- {note}" for note in notes)) if notes else ""
    return f"{submitted}\n\n{state}{said}"


def _finish(state: Progress, intake_path: str, questions: List[str],
            findings: Dict[str, List[str]], structure: Dict[str, list],
            workspace_root: Path) -> Progress:
    state.gaps_now = len(outstanding(intake_path)) if Path(intake_path).exists() else 0
    state.questions = list(questions)
    state.findings = dict(findings)
    if findings or structure:
        _write_evidence(Path(workspace_root), findings, structure)
    logger.info("Intake loop: %d turn(s), %d call(s), %d gap(s) left, %d question(s). Stopped "
                "because %s.", state.turns, state.calls, state.gaps_now, len(state.questions),
                state.stopped_because)
    return state


def _write_evidence(root: Path, findings: Dict[str, List[str]],
                    structure: Dict[str, list]) -> None:
    """The context every later stage is grounded on, rendered from what the loop filed.

    Written in the same two files the sequence this replaces wrote, in the same formats, because
    six stages downstream read them and none of them should be able to tell which path produced
    the run. The graph is the workbook; this is everything else the documents established, which
    the workbook has no column for and the writer cannot describe a route without.
    """
    from ..core.evidence import FACETS, EvidenceRecord, FacetAnswer
    from .context_document import build_context_document
    from .extraction import record_to_json

    record = EvidenceRecord(structure=dict(structure))
    record.answers = [
        FacetAnswer(facet=facet,
                    answer=" ".join(findings.get(facet, [])),
                    points=list(findings.get(facet, [])),
                    confidence="Medium" if findings.get(facet) else "Low")
        for facet in FACETS]
    try:
        (root / "ingest_context.md").write_text(build_context_document(record), encoding="utf-8")
        record_to_json(record, root / "ingest_evidence.json")
    except OSError as exc:
        logger.warning("Could not write what the loop established: %s", exc)
