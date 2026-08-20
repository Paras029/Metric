"""Reading a submitted pack and filling the intake, as one loop."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set

from metric.phases.intake.intake.gaps import find_gaps
from metric.domain.graph import DecisionGraph
from metric.domain.intake import read_intake
from metric.llm import config, prompts

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
    """Everything structurally wrong with the declaration on disk, as sentences."""
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
    """Whether a question the model wants to ask is one a person can actually act on."""
    text = str(question or "").strip()
    if len(text) < 15:
        return False
    named = {word.strip(".,:;\"'") for word in text.replace("(", " ").replace(")", " ").split()}
    return any(any(token in named for token in problem.split(":")[0].split())
               for problem in problems)


@dataclass
class Tool:
    """One thing the loop can do, as a name, a description and a callable."""

    name: str
    description: str
    run: Callable[..., str]


class Pack:
    """Everything submitted, parsed and redacted once, before any model sees it."""

    def __init__(self, root: Path, should_redact=None) -> None:
        from metric.phases.intake.intake.groups import evidence_files
        from metric.phases.intake.intake.readers import is_image

        self.root = Path(root)
        self.should_redact = should_redact or (lambda path: False)
        self.files: Dict[str, Path] = {
            path.name: path
            for paths in evidence_files(self.root).values() for path in paths}
        self.documents = {n: p for n, p in self.files.items() if not is_image(p)}
        self.diagrams = {n: p for n, p in self.files.items() if is_image(p)}
        self._text: Dict[str, str] = {}
        self._mapping = None

    def text_of(self, name: str) -> str:
        """One document as locatable, redacted text. Parsed once and remembered."""
        from metric.phases.intake.intake.readers import UnreadableDocument, read_document
        from metric.phases.intake.intake.redaction import redact_segments

        if name in self._text:
            return self._text[name]
        path = self.documents[name]
        try:
            _, segments = read_document(path)
        except UnreadableDocument as exc:
            self._text[name] = f"{name} could not be read: {exc}"
            return self._text[name]
        segments, self._mapping = redact_segments(
            segments, self._mapping, force=self.should_redact(path))
        self._text[name] = "\n\n".join(f"[{s.locator}]\n{s.text}" for s in segments)
        return self._text[name]


def build_tools(pack: "Pack", intake_path: str, read_once: Dict[str, object],
                structure: Dict[str, list], describe_images=None,
                diagram_mode: str = "split") -> List[Tool]:
    """The loop's whole surface: read what was submitted, write what it establishes, check itself."""
    from metric.phases.intake.intake.readers import is_image

    already_read: Set[str] = set()

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
        # A second read returns a pointer rather than the document. The text is already in this
        # conversation, so sending it again buys nothing and costs the whole document in tokens --
        # and a loop that re-reads what it has already read is a loop about to repeat what it
        # already concluded, which is the thing being guarded against.
        if key in already_read:
            return (f"{key} is already in this conversation, above -- re-read it there. If it did "
                    f"not answer the question, it does not, and something else has to.")
        already_read.add(key)
        return pack.text_of(key)

    def read_the_pack(**_) -> str:
        """What the submitted pack establishes, read with the pipeline built to do it."""
        if not pack.files:
            return "Nothing was submitted, so there is nothing to read."
        return (_read_the_pack_once(pack, read_once, structure, describe_images, diagram_mode)
                or "The pack could not be read.")

    def what_is_declared(**_) -> str:
        from metric.llm.describe import describe_graph, describe_use_case
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

    return [
        Tool("list_sources", "List every submitted file, and whether each is a document or a "
                             "workflow diagram. Call this first.", list_sources),
        Tool("read_document", "Read one submitted document in full, with its page markers. "
                              "Takes the file name exactly as list_sources gave it.",
             read_one_document),
        Tool("read_the_pack", "Read every submitted document and workflow diagram together, and "
                              "return what they establish about the agent, with the graph read "
                              "off any diagrams. Almost always the first thing to do.",
             read_the_pack),
        Tool("what_is_declared", "The declaration as it currently stands: the use case, and the "
                                 "decision graph with every edge.", what_is_declared),
        Tool("audit_declaration", "What is still structurally wrong with the declaration. This "
                                  "is what decides whether the work is finished, so check it "
                                  "before concluding anything is.", audit),
        Tool("write_declaration", "Write the declaration. Takes a JSON object with use_case, "
                                  "personas, capabilities, decisions, states and tools. Replaces "
                                  "what is there, so send the whole declaration every time, not "
                                  "a patch.", write),
    ]


def _read_the_pack_once(pack: "Pack", read_once: Dict[str, object], structure: Dict[str, list],
                        describe_images, diagram_mode: str) -> str:
    """Run the reading pipeline over everything submitted, once, and return what it established."""
    from metric.phases.intake.intake.context_document import build_context_document
    from metric.phases.intake.intake.extraction import extract_documents

    if read_once.get("record") is not None:
        return build_context_document(read_once["record"])
    if not pack.files:
        return ""
    try:
        record = extract_documents(
            list(pack.files.values()), should_redact=pack.should_redact,
            describe_images=describe_images, diagram_mode=diagram_mode)
    except Exception as exc:
        logger.warning("The pack could not be read: %s", exc)
        return ""

    read_once["record"] = record
    structure.update(record.structure or {})
    return build_context_document(record)


def _empty_record():
    from metric.phases.intake.intake.evidence import EvidenceRecord
    return EvidenceRecord()


def _write_declaration(declaration: str, intake_path: str, structure: Dict[str, list]) -> str:
    """Validate a proposed declaration and write it, or say exactly why it was not written."""
    from metric.shared import parse_json_object
    from metric.phases.intake.intake.drafting import DraftedIntake, _consolidated, _validate, carry_diagram_through
    from metric.phases.intake.intake.drafting import write_drafted_intake

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
    """A tool for the one thing the loop cannot do for itself: ask a person."""
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
    """One exchange with a tool-calling model, in a shape the loop can be tested without one."""

    def __init__(self, tools: Sequence[Tool], tier=None) -> None:
        self._tools = list(tools)
        self._tier = tier or config.JUDGEMENT
        self._model = None

    def _bound(self):
        if self._model is None:
            from langchain_core.tools import StructuredTool

            from metric.llm.gateway import tool_model
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
        should_redact=None, describe_images=None, notes: Sequence[str] = (),
        diagram_mode: str = "split") -> Progress:
    """Read the pack and fill the declaration, and stop when it is complete or the budget is spent."""
    report = progress or (lambda *args, **kwargs: None)
    pack = Pack(Path(workspace_root), should_redact=should_redact)
    read_once: Dict[str, object] = {}
    structure: Dict[str, list] = {}
    questions: List[str] = []

    # Read the pack before the loop is given a turn, rather than offering it as a tool and hoping
    # it is called. Two things went wrong when it was optional. A loop that opened files one at a
    # time with read_document never produced a record, so no context document was written -- and
    # the context document is what every later stage is grounded on, since none of them see the
    # documents. And the reading itself was worse, because reading documents together is what lets
    # a threshold in an appendix meet the process it governs in section three.
    reading = _read_the_pack_once(pack, read_once, structure, describe_images, diagram_mode)
    if read_once.get("record") is not None:
        _write_evidence(Path(workspace_root), read_once["record"])

    problems = outstanding(intake_path)
    state = Progress(gaps_at_start=len(problems), gaps_now=len(problems))
    started_from_nothing = not Path(intake_path).exists()
    if not problems and not started_from_nothing:
        state.stopped_because = "the declaration was already structurally complete"
        return _finish(state, intake_path, questions, read_once, structure, workspace_root)

    tools = build_tools(pack, intake_path, read_once, structure,
                        describe_images=describe_images, diagram_mode=diagram_mode)
    tools.append(build_question_tool(problems, questions))
    by_name = {tool.name: tool for tool in tools}
    converse = converse or Conversation(tools)

    messages: List[dict] = [
        {"role": "system", "content": prompts.load("shared.cds") + "\n\n"
                                      + prompts.render(
                                          _SYSTEM_PROMPT,
                                          wiring=prompts.load("shared.wiring"))},
        {"role": "human", "content": _opening(pack, problems, started_from_nothing, notes,
                                              reading)},
    ]

    repeated: Dict[str, object] = {}
    for sweep in range(1, sweeps + 1):
        while state.turns < max_turns:
            state.turns += 1
            report(_doing(state), state.turns, max_turns)
            try:
                reply = converse(messages)
            except Exception as exc:
                logger.warning("The loop's model call failed on turn %d: %s", state.turns, exc)
                state.stopped_because = f"a model call failed: {exc}"
                return _finish(state, intake_path, questions, read_once, structure, workspace_root)
            state.calls += 1

            if not reply.get("tool_calls"):
                break

            # A turn that asks for exactly what the last turn asked for gets the same answer, so
            # the turn after it asks again. Two of those is a loop, and a loop at judgement tier
            # is expensive in a way nothing on screen makes obvious. Said once, then ended: the
            # model is told what it is doing, and if it does it again the sweep is over.
            signature = tuple(sorted((call["tool"], json.dumps(call.get("args") or {}, sort_keys=True))
                                     for call in reply["tool_calls"]))
            if signature == repeated.get("last"):
                repeated["count"] = repeated.get("count", 0) + 1
                if repeated["count"] >= 2:
                    logger.info("The loop asked for the same thing three turns running; ending "
                                "the sweep rather than going round again.")
                    break
            else:
                repeated["last"], repeated["count"] = signature, 0

            messages.append({"role": "assistant", "content": reply.get("content", ""),
                             "raw_tool_calls": reply.get("raw_tool_calls", [])})
            if repeated.get("count"):
                messages.append({
                    "role": "human",
                    "content": "That is the same call as the last turn, and it returned the same "
                               "thing. Either act on what it already told you, or record a "
                               "question and stop."})
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
            return _finish(state, intake_path, questions, read_once, structure, workspace_root)
        if state.turns >= max_turns:
            state.stopped_because = f"the budget of {max_turns} turns was spent"
            return _finish(state, intake_path, questions, read_once, structure, workspace_root)

        closed = [problem for problem in problems if problem not in remaining]
        stuck = [problem for problem in remaining if problem in problems]

        # A sweep that closed nothing will not be followed by one that does. Everything the second
        # sweep could read, the first one could read; what is left at that point is what the
        # documents do not say, and another pass over them is the loop going round in circles at
        # judgement-tier prices. This is the stop that matters most in practice.
        if not closed:
            state.stopped_because = (
                f"a full sweep closed nothing, so {len(remaining)} gap(s) are not in the "
                f"documents")
            return _finish(state, intake_path, questions, read_once, structure, workspace_root)

        messages.append({"role": "human", "content": _next_sweep(closed, stuck, remaining)})
        problems = remaining

    state.stopped_because = (f"{sweeps} sweeps finished with {state.gaps_now} left, which the "
                             f"documents do not appear to settle")
    return _finish(state, intake_path, questions, read_once, structure, workspace_root)


def _doing(state: "Progress") -> str:
    """What the loop is doing right now, in the words of what it last did."""
    if not state.tools_run:
        return "Reading the pack and filling the declaration"
    return _TOOL_LINES.get(state.tools_run[-1], "Working through the declaration")


_TOOL_LINES = {
    "list_sources": "Looking at what was submitted",
    "read_document": "Reading a submitted document",
    "read_diagram": "Reading a workflow diagram",
    "what_is_declared": "Re-reading the declaration",
    "audit_declaration": "Checking what the declaration still needs",
    "write_declaration": "Writing the declaration",
    "record_finding": "Recording what the documents establish",
    "ask_the_model_owner": "Noting a question for the model owner",
}


def _opening(pack: "Pack", problems: Sequence[str], from_nothing: bool,
             notes: Sequence[str], reading: str = "") -> str:
    """The first message: what the documents established, what exists, and what is wrong with it."""
    submitted = ("Submitted: "
                 + ", ".join(sorted(pack.files)) if pack.files else
                 "Nothing was submitted.")
    if reading:
        submitted = f"WHAT THE SUBMITTED PACK ESTABLISHES\n\n{reading}\n\n{submitted}"
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


def _next_sweep(closed: Sequence[str], stuck: Sequence[str],
                remaining: Sequence[str]) -> str:
    """What to say at a sweep boundary, given what moved."""
    said = [f"Since the last sweep, {len(closed)} settled: "
            + "; ".join(closed[:4]) + ("; …" if len(closed) > 4 else "")]
    if stuck:
        said.append(
            "These were outstanding last sweep too, and reading for them again has not settled "
            "them:\n" + "\n".join(f"- {problem}" for problem in stuck)
            + "\n\nDo not read the same documents again for these. Either the answer is "
              "somewhere you have not looked, or the documents do not contain it -- in which case "
              "record a question with ask_the_model_owner and move on.")
    fresh = [problem for problem in remaining if problem not in stuck]
    if fresh:
        said.append("Newly outstanding, from what you just wrote:\n"
                    + "\n".join(f"- {problem}" for problem in fresh))
    return "\n\n".join(said)


def _finish(state: Progress, intake_path: str, questions: List[str],
            read_once: Dict[str, object], structure: Dict[str, list],
            workspace_root: Path) -> Progress:
    state.gaps_now = len(outstanding(intake_path)) if Path(intake_path).exists() else 0
    state.questions = list(questions)
    record = read_once.get("record")
    if record is not None:
        _write_evidence(Path(workspace_root), record)
        state.findings = {answer.facet: list(answer.points)
                          for answer in record.answers if answer.points}
    logger.info("Intake loop: %d turn(s), %d call(s), %d gap(s) left, %d question(s). Stopped "
                "because %s.", state.turns, state.calls, state.gaps_now, len(state.questions),
                state.stopped_because)
    return state


def _write_evidence(root: Path, record) -> None:
    """The context every later stage is grounded on, and the record behind it."""
    from metric.phases.intake.intake.context_document import build_context_document
    from metric.phases.intake.intake.extraction import record_to_json

    try:
        (root / "ingest_context.md").write_text(build_context_document(record), encoding="utf-8")
        record_to_json(record, root / "ingest_evidence.json")
    except OSError as exc:
        logger.warning("Could not write what the loop established: %s", exc)
