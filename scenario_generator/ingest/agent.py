"""Filling the intake as a loop rather than as a fixed sequence of calls.

The pipeline that drafts an intake is a good pipeline and this does not replace it. Documents are
still parsed, redacted and read in parallel; diagrams are still read box by box and audited
against their own picture; the draft is still carried through deterministically. What that
sequence cannot do is *notice*. It makes its calls in a fixed order, writes what comes back, and
stops -- so a declaration with a decision that leads nowhere, or a block nothing hands on to, is
finished as far as the pipeline is concerned, and the only thing that reads it afterwards is a
person.

This runs the same readers under a goal instead: fill the intake, keep going until it audits
clean or the budget is spent. Three things make that safe to run against a model:

**The termination oracle is deterministic.** Whether the declaration is finished is decided by
:func:`core.gaps.find_gaps` and the graph audit, never by the model saying it is done. A loop that
judges its own completion runs until it feels like stopping, which on a bad day is never and on a
worse day is immediately.

**Every tool is an existing, tested reader.** Nothing here parses a document or reads an image; it
calls the code that already does, so the loop cannot be a second way of doing the same job that
drifts from the first.

**The budget is hard and the work is idempotent.** Each turn is a bounded number of calls, the
declaration on disk is the state, and stopping at any point leaves a workbook that is exactly as
good as the last thing written to it. There is no half-applied turn.

The pause is deliberate. After the sweeps the loop stops and puts what it could not settle to a
person, because the questions that remain at that point are the ones no amount of re-reading will
answer -- what the documents do not say. Those questions are filtered in code against the audit,
so every one names a specific row and a specific hole rather than asking whether a decision is
clear.
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

# What a full run is allowed to spend, and what it aims to. The cap is a backstop against a loop
# that will not settle; the target is what a pack of ordinary size actually costs, and going past
# it is a signal that the declaration is not converging rather than that it is large.
MAX_TURNS = 24
TARGET_TURNS = 16

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

    problems = [f"{gap.target_id}: {gap.question}" for gap in find_gaps(intake)]
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


def build_tools(workspace_root: Path, intake_path: str) -> List[Tool]:
    """The loop's whole surface: read what was submitted, read what is declared, write it back.

    Every one wraps a reader that already exists and is already tested. Nothing here parses a
    document or looks at an image on its own account -- a second implementation of either would be
    a second thing to keep in step with the first, and the first is the one that has been through
    redaction and grounding.
    """
    from .groups import evidence_files
    from .readers import UnreadableDocument, is_image, read_document

    files = {path.name: path
             for paths in evidence_files(workspace_root).values() for path in paths}

    def list_sources(**_) -> str:
        if not files:
            return ("Nothing was submitted. The declaration has to be built from what is already "
                    "in the workbook, or a question has to go to the model owner.")
        return json.dumps([{"name": name, "kind": "diagram" if is_image(path) else "document"}
                           for name, path in sorted(files.items())], indent=2)

    def read_one_document(name: str = "", **_) -> str:
        path = files.get(str(name).strip())
        if path is None:
            return f"There is no submitted file called {name!r}. Call list_sources first."
        if is_image(path):
            return f"{name} is an image; call read_diagram for it."
        try:
            reference, segments = read_document(path)
        except UnreadableDocument as exc:
            return f"{name} could not be read: {exc}"
        return "\n\n".join(f"[{segment.locator}]\n{segment.text}" for segment in segments)

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

    return [
        Tool("list_sources", "List every submitted file, and whether each is a document or a "
                             "workflow diagram. Call this first.", list_sources),
        Tool("read_document", "Read one submitted document in full, with its page markers. "
                              "Takes the file name exactly as list_sources gave it.",
             read_one_document),
        Tool("what_is_declared", "The declaration as it currently stands: the use case, and the "
                                 "decision graph with every edge.", what_is_declared),
        Tool("audit_declaration", "What is still structurally wrong with the declaration. This "
                                  "is what decides whether the work is finished, so check it "
                                  "before concluding anything is.", audit),
    ]


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
        max_turns: int = MAX_TURNS, sweeps: int = SWEEPS_BEFORE_PAUSE) -> Progress:
    """Work towards a complete declaration, and stop when it is complete or the budget is spent.

    Returns what happened rather than the declaration: the declaration is the workbook on disk,
    which every tool reads and the caller already has a path to. Nothing here is left half-applied
    -- a run stopped at any point leaves the workbook exactly as good as the last thing written.
    """
    report = progress or (lambda *args, **kwargs: None)
    problems = outstanding(intake_path)
    state = Progress(gaps_at_start=len(problems), gaps_now=len(problems))
    if not problems:
        state.stopped_because = "the declaration was already structurally complete"
        return state

    questions: List[str] = []
    tools = build_tools(Path(workspace_root), intake_path)
    tools.append(build_question_tool(problems, questions))
    by_name = {tool.name: tool for tool in tools}
    converse = converse or Conversation(tools)

    messages: List[dict] = [
        {"role": "system", "content": prompt_loader.load(_SYSTEM_PROMPT)},
        {"role": "human", "content":
            "The declaration is incomplete. What is wrong with it:\n\n"
            + "\n".join(f"- {problem}" for problem in problems)
            + "\n\nRead whatever was submitted and settle as many of these as the documents "
              "allow. Check audit_declaration before concluding anything is finished."},
    ]

    for sweep in range(1, sweeps + 1):
        while state.turns < max_turns:
            state.turns += 1
            report(f"Working through what the declaration still needs (sweep {sweep})",
                   state.turns, max_turns)
            try:
                reply = converse(messages)
            except Exception as exc:
                logger.warning("The loop's model call failed on turn %d: %s", state.turns, exc)
                state.stopped_because = f"a model call failed: {exc}"
                return _finish(state, intake_path, questions)
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
        if not remaining:
            state.stopped_because = "the declaration audits clean"
            return _finish(state, intake_path, questions)
        if state.turns >= max_turns:
            state.stopped_because = f"the budget of {max_turns} turns was spent"
            return _finish(state, intake_path, questions)

        problems = remaining
        messages.append({
            "role": "human",
            "content": "Still outstanding:\n\n"
                       + "\n".join(f"- {problem}" for problem in remaining)
                       + "\n\nSettle what the documents settle. Record a question for the model "
                         "owner only where they genuinely do not."})

    state.stopped_because = (f"{sweeps} sweeps finished with {state.gaps_now} left, which the "
                             f"documents do not appear to settle")
    return _finish(state, intake_path, questions)


def _finish(state: Progress, intake_path: str, questions: List[str]) -> Progress:
    state.gaps_now = len(outstanding(intake_path))
    state.questions = list(questions)
    logger.info("Intake loop: %d turn(s), %d call(s), %d gap(s) left, %d question(s). Stopped "
                "because %s.", state.turns, state.calls, state.gaps_now, len(state.questions),
                state.stopped_because)
    return state
