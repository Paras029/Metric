"""Does this gateway support an agent loop? Run it, paste the output back.

The intake stage is being rebuilt as a loop: one goal, a set of tools, and a model that keeps
reading and filling until the declaration audits clean. Whether that can be built on LangChain's
own tool-calling -- or has to be hand-rolled on JSON in text -- depends on things nobody can look
up, because they depend on what SafeChain hands back and what the gateway model behind it does.

Nine checks, each one deciding something specific about the design. Every one is guarded, so a
failure reports itself and the rest still run. Nothing is written anywhere and no project file is
touched: this reads your .env and config.yml exactly as a normal run would, and spends a handful
of model calls.

    python tools/probe_agent_support.py

Add --model NAME to probe a model other than the one LLM_MODEL_ID names.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A small PNG that actually contains a flow: two boxes joined by an arrow, one labelled A and
# one labelled B. It has to have something in it. The first version of this probe sent a 1x1 white
# pixel, and the model reported -- correctly -- that it could not see anything, which read as
# "vision does not work in a tool conversation" when all it showed was that a blank image is
# blank. An image check needs an image.
_FLOW_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAQQAAABaCAIAAADRmb9uAAABmklEQVR42u3bUY7CMBBEQd//0iDlKwIlMhKxx+6q"
    "CySD5wELbHsBh+YhADGAGEAMIAYQA4gBxABiADGAGEAMIAYQA4gBxABiADGAGEAMIAYQA4gBxAB7xNDCxB2/8/0p"
    "hqjNCIzBsGIQg/MVg2GdrxhshvMVg80wshhshpHFYDOMLAabYWQx2Awji8FmGFkMNsPIYrAZRg6MYfDVN96Mq9GG"
    "jVzh95FiEMPddBUWUQy9tz7yBrZ/z/A9oBjEkPsG+mNGMSwQw/m6dR6s/XoQgxiiYzhPKobqMcx6axv4OaMYxHB5"
    "3UDDztRHq3+74wF34pXBK0O5GGY9f/ibQQyF9uP+ck/fjE+TxCCGoBh8z7BGDD3XevR+fAMtBjFExDD9t0k+TVp+"
    "XfYezfmKwbDOVwyGdb5isBlGFoPNMLIYbIaRxWAzjCwGm2FkMdgMI4vBZhhZDDbDyGKwGUaeGIP/gdx7P5xvbwwQ"
    "9NTgIQAxgBhADCAGEAOIAcQAYgAxgBhADCAGEAOIAcQAYgAxgBhADCAGEAOIAcQAi3gDeHKb8SP/JPkAAAAASUVO"
    "RK5CYII=")

findings: dict = {}


def check(name: str, decides: str):
    """Run one check, record what it found, and never let it stop the others."""
    def wrap(fn):
        print(f"\n{'─' * 78}\n{name}\n  decides: {decides}")
        started = time.time()
        try:
            result = fn()
            findings[name] = result
            print(f"  → {result}   [{time.time() - started:.1f}s]")
        except Exception as exc:
            findings[name] = f"FAILED: {type(exc).__name__}: {exc}"
            print(f"  → FAILED after {time.time() - started:.1f}s")
            print("    " + "\n    ".join(traceback.format_exc().strip().splitlines()[-4:]))
        return fn
    return wrap


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="probe a model other than LLM_MODEL_ID")
    args = parser.parse_args()

    from scenario_generator.llm import config
    from scenario_generator.llm.gateway import _load_safechain, chat_model

    model_id = args.model or config.LLM_MODEL_ID
    print(f"Interpreter : {sys.executable}")
    print(f"Model       : {model_id}")
    print(f"Vision      : {'on' if config.LLM_VISION else 'off'}")

    try:
        raw = _load_safechain()(model_id)
    except Exception as exc:
        print(f"\nSafeChain could not build a model, so nothing below can run:\n  {exc}\n\n"
              f"This is the same path an ordinary run takes, so fix it here and the tool works\n"
              f"too. `python tools/find_safechain.py` reports which of those two it is.")
        return 1

    # ---------------------------------------------------------------- 1. what we are handed
    @check("1. What SafeChain returns",
           "whether tool-calling is reachable at all, or everything must be JSON in text")
    def _one():
        from langchain_core.language_models import BaseChatModel
        kind = type(raw).__mro__
        return {
            "type": type(raw).__name__,
            "is_BaseChatModel": isinstance(raw, BaseChatModel),
            "has_bind_tools": hasattr(raw, "bind_tools"),
            "has_with_structured_output": hasattr(raw, "with_structured_output"),
            "mro": [c.__name__ for c in kind][:5],
        }

    # ---------------------------------------------------------------- 2. the project's wrapper
    @check("2. Does the project's own chat_model() keep bind_tools?",
           "whether the agent can reuse chat_model(), or needs to bind tools before the "
           "parameter bind. .bind() returns a RunnableBinding, which has no bind_tools.")
    def _two():
        wrapped = chat_model(tier=config.JUDGEMENT, model=model_id)
        return {"type": type(wrapped).__name__, "has_bind_tools": hasattr(wrapped, "bind_tools")}

    # ---------------------------------------------------------------- 3. one tool call
    @check("3. Does the model actually emit a tool call?",
           "the whole design. No tool_calls here means a hand-rolled JSON loop.")
    def _three():
        from langchain_core.tools import tool

        @tool
        def look_up_decision(decision_id: str) -> str:
            """Return what a declared decision says. Use this rather than guessing."""
            return "unused in the probe"

        reply = raw.bind_tools([look_up_decision]).invoke(
            [("system", "You have tools. Use them rather than answering from memory."),
             ("human", "What does decision DEC-03 say? Look it up.")])
        calls = getattr(reply, "tool_calls", None)
        return {"tool_calls": calls,
                "emitted": bool(calls),
                "text_instead": (reply.content or "")[:120] if not calls else ""}

    # ---------------------------------------------------------------- 4. the loop closes
    @check("4. Can a tool result be fed back for a second turn?",
           "whether a loop is possible at all, or only one tool call per request")
    def _four():
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
        from langchain_core.tools import tool

        @tool
        def look_up_decision(decision_id: str) -> str:
            """Return what a declared decision says."""
            return "unused in the probe"

        bound = raw.bind_tools([look_up_decision])
        history = [SystemMessage("You have tools. Use them rather than answering from memory."),
                   HumanMessage("What does decision DEC-03 say? Look it up.")]
        first = bound.invoke(history)
        if not getattr(first, "tool_calls", None):
            return "no first tool call, so the loop cannot be tested"

        call = first.tool_calls[0]
        history += [first, ToolMessage(
            content="DEC-03 'Timeout check' has outcomes Timeout and Ok.",
            tool_call_id=call["id"])]
        second = bound.invoke(history)
        return {"second_turn_text": (second.content or "")[:160],
                "used_the_result": "timeout" in (second.content or "").lower(),
                "asked_for_another_tool": bool(getattr(second, "tool_calls", None))}

    # ---------------------------------------------------------------- 5. several at once
    @check("5. Can it ask for several tools in one turn?",
           "whether the loop can read three documents in parallel or must go one at a time -- "
           "this is most of the wall-clock difference on a real pack")
    def _five():
        from langchain_core.tools import tool

        @tool
        def read_document(name: str) -> str:
            """Read one submitted document and report what it establishes."""
            return "unused in the probe"

        reply = raw.bind_tools([read_document]).invoke(
            [("system", "You have tools. Call them for every document named."),
             ("human", "Read all three: policy.pdf, flow.docx and limits.xlsx.")])
        calls = getattr(reply, "tool_calls", []) or []
        return {"calls_in_one_turn": len(calls),
                "names": [c.get("name") for c in calls]}

    # ---------------------------------------------------------------- 6. images and tools together
    @check("6. Do images survive in a tool-calling conversation?",
           "whether the agent can look at a diagram mid-loop, or images need a separate "
           "non-agent call the way they work today")
    def _six():
        if not config.LLM_VISION:
            return "skipped: LLM_VISION is off"
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_core.tools import tool

        @tool
        def record_box(label: str, kind: str) -> str:
            """Record one box read off a workflow diagram."""
            return "unused in the probe"

        encoded = base64.b64encode(_FLOW_PNG).decode()
        message = HumanMessage(content=[
            {"type": "text", "text": "This image shows two boxes joined by an arrow. Call "
                                     "record_box once for each box you can see."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}])
        reply = raw.bind_tools([record_box]).invoke(
            [SystemMessage("You have tools. Use them rather than describing the picture."),
             message])
        calls = getattr(reply, "tool_calls", []) or []
        return {"request_accepted": True,
                "tool_calls": len(calls),
                "saw_the_image": bool(calls) or "box" in (reply.content or "").lower(),
                "args": [c.get("args") for c in calls][:4],
                "text": (reply.content or "")[:140]}

    # ---------------------------------------------------------------- 7. the fallback
    @check("7. Does with_structured_output work?",
           "the fallback if tools are unavailable: schema-shaped replies without free-text JSON "
           "parsing. Worth knowing even if tools work.")
    def _seven():
        from pydantic import BaseModel, Field

        class Decision(BaseModel):
            """One branch point in an agent's flow."""
            name: str = Field(description="what the decision is called")
            outcomes: list[str] = Field(description="the named ways it can resolve")

        out = raw.with_structured_output(Decision).invoke(
            "An identity check that either passes or fails. Describe it.")
        return {"type": type(out).__name__, "value": str(out)[:120]}

    # ---------------------------------------------------------------- 8. the real ceiling
    @check("8. What output ceiling does this model accept?",
           "the per-call budget. The agent's turns are short, but a full intake proposal is not.")
    def _eight():
        results = {}
        for cap in (4096, 16384, 32768, 65536):
            try:
                raw.bind(max_tokens=cap).invoke("Reply with the single word OK.")
                results[cap] = "accepted"
            except Exception as exc:
                results[cap] = f"rejected: {str(exc)[:90]}"
                break
        return results

    # ---------------------------------------------------------------- 9. what a turn costs
    @check("9. How long does one short turn take?",
           "the call budget in wall-clock terms. A 20-call loop at 8s a call is three minutes.")
    def _nine():
        timings = []
        for _ in range(3):
            started = time.time()
            raw.invoke("Reply with the single word OK.")
            timings.append(round(time.time() - started, 1))
        return {"seconds_per_turn": timings, "twenty_calls_would_be":
                f"~{sum(timings) / len(timings) * 20 / 60:.1f} minutes if run one at a time"}

    # ---------------------------------------------------------------- verdict
    print(f"\n{'═' * 78}\nWHAT THIS MEANS\n{'═' * 78}")
    tools_work = isinstance(findings.get("3. Does the model actually emit a tool call?"), dict) \
        and findings["3. Does the model actually emit a tool call?"].get("emitted")
    loop_works = isinstance(findings.get("4. Can a tool result be fed back for a second turn?"),
                            dict)

    if tools_work and loop_works:
        print("Tool-calling and multi-turn both work. The loop is LangChain's own, tools are typed\n"
              "functions, and the existing readers become those tools unchanged.")
    elif tools_work:
        print("Tool calls are emitted but the loop did not close. Look at check 4 -- an agent\n"
              "needs the second turn, so this is the thing to solve before anything is built.")
    else:
        print("No tool calls. The loop has to be hand-rolled: the model returns a JSON action, the\n"
              "code dispatches it and returns the result as the next message. Buildable -- the\n"
              "council already orchestrates multi-call work -- but every step is a parse that can\n"
              "fail, and check 7 decides whether structured output makes that safer.")

    print("\nPaste everything above back. The raw JSON follows.\n")
    print(json.dumps(findings, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
