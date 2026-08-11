"""Three models on one judgement, where a second opinion is worth what it costs.

The three passes this can be turned on for -- reading the documents into an intake, reviewing the
whole scenario space, and mapping a model owner's conversations onto it -- have a property the
others do not. Each is a *judgement over a whole body of evidence*, made once, that everything
downstream is then built on. A worse intake is a worse scenario space, permanently. A worse review
is a materiality tier nobody revisits. There is no later stage that would catch either, because
every later stage takes them as given.

Compare that with writing a scenario up, or assigning it a tier. Those are made per scenario,
hundreds of times, over a small and legible input, and a bad one is visible on the page next to
its neighbours. Paying three times for those would triple the bill of the whole run to improve
the part of it a person can already see and fix. So a council is deliberately not available there.

**How it runs.** Two workers answer the same prompt independently, in parallel, knowing nothing
of each other -- which is the point: two models shown each other's work agree with each other,
and the agreement means nothing. Then a reconciler answers the same prompt *itself* and is shown
both workers' replies as material. It is not a vote and not a merge. A vote over two answers
cannot break a tie, and a merge of two JSON documents is a document neither model wrote and
neither would defend. What the reconciler produces is a third answer, made with two others in
view, and it is the one that is used.

**Everything is off by default.** A council names its models in ``tuning.yml``; naming none means
one model does the work exactly as it did before, and no code path changes.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Sequence, Tuple, Union

from . import cancellation, config
from .calling import call, call_batch

logger = logging.getLogger(__name__)

# The only passes a council can be turned on for, and the reason is in the module docstring: each
# is one judgement over a whole body of evidence that every later stage takes as given.
COUNCIL_STAGES = ("INTAKE_DRAFT", "INTAKE_REPAIR", "REVIEWER_ASSESS", "REVIEWER_PROPOSE",
                  "COVERAGE_MAP")

# Appended to the worker prompt to make the reconciler's. Deliberately not a separate prompt file:
# the reconciler is doing the *same task*, against the same instructions, and a second set of
# instructions is a second thing to keep in step with the first.
_RECONCILE_BLOCK = """

TWO OTHER READINGS OF THIS SAME TASK

Two models were given everything above and answered independently. Neither saw the other, and
neither is authoritative — they are shown to you as material, not as a verdict.

Do the task yourself, with these in view. Where they agree, that agreement is evidence and is
usually right. Where they differ, decide, and prefer whichever the evidence above actually
supports rather than splitting the difference — an answer halfway between two readings is one
neither of them made and one you cannot defend.

Where one has found something the other missed, keep it. A reading is not better for being
shorter.

FIRST READING

{first}

SECOND READING

{second}

Now return your own answer, in exactly the shape the instructions above ask for. Return only that
— no commentary on the two readings, and no explanation of what you changed.
"""


@dataclass(frozen=True)
class Council:
    """Two workers and a reconciler, as model names."""

    workers: Tuple[str, ...]
    reconciler: str

    def __bool__(self) -> bool:
        """Whether this is a council at all: two workers and someone to settle them."""
        return len(self.workers) >= 2 and bool(self.reconciler)


def for_stage(stage: str) -> Optional[Council]:
    """The council configured for this call site, or ``None`` for the ordinary single call.

    Read at the point of use rather than built once, for the same reason every other setting here
    is a function: the interface runs for hours and a value fixed at import cannot be changed
    without restarting it.
    """
    if stage.upper() not in COUNCIL_STAGES:
        return None

    key, prefix = stage.lower(), f"LLM_COUNCIL_{stage.upper()}"
    if not _truthy(config.setting(f"{prefix}_ENABLED", "council", "stages", key, default="")
                   or config.setting("LLM_COUNCIL_ENABLED", "council", "enabled", default="off")):
        return None

    workers = _models(config.setting("LLM_COUNCIL_WORKERS", "council", "workers", default=""))
    reconciler = str(config.setting("LLM_COUNCIL_RECONCILER", "council", "reconciler",
                                    default="") or "").strip()
    council = Council(workers=tuple(workers), reconciler=reconciler)
    if not council:
        logger.warning("A council is switched on for %s but it names %d worker(s) and %s "
                       "reconciler, so the ordinary single call is made instead.",
                       stage, len(workers), "a" if reconciler else "no")
        return None
    return council


def _models(raw) -> List[str]:
    """A worker list from YAML, which gives a list, or from an environment variable, which gives
    a comma-separated string."""
    if isinstance(raw, (list, tuple)):
        values = [str(item).strip() for item in raw]
    else:
        values = [item.strip() for item in str(raw or "").split(",")]
    return [value for value in values if value]


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def deliberate(complete: Callable[..., str], system: str, user: str, *, stage: str, tier,
               cancel=None) -> str:
    """Two workers in parallel, then a reconciler that answers with both in view.

    Returns the reconciler's reply, which the caller parses exactly as it parses a single call --
    a council changes who answers, never the shape of the answer.

    Degrades rather than fails, at every step. One worker that does not come back leaves the
    reconciler with one reading, which is still better than none. Both failing, or the reconciler
    failing, falls back to the ordinary single call on the stage's own model, so switching a
    council on can slow a run down and cannot break one.
    """
    council = for_stage(stage)
    if council is None:
        return call(complete, system, user, tier=tier)

    cancellation.check(cancel)
    readings = _workers(complete, system, user, council, tier, cancel)
    if not readings:
        logger.warning("Neither council worker answered for %s; falling back to a single call.",
                       stage)
        return call(complete, system, user, tier=tier)

    cancellation.check(cancel)
    reconciled = user + _RECONCILE_BLOCK.format(
        first=readings[0],
        second=readings[1] if len(readings) > 1 else "(this model did not answer)")
    try:
        return call(complete, system, reconciled,
                    tier=replace(tier, model=council.reconciler,
                                 name=f"{tier.name}:reconciler"))
    except Exception as exc:
        logger.warning("The council's reconciler did not answer for %s (%s); using the first "
                       "reading instead.", stage, exc)
        return readings[0]


def _workers(complete, system: str, user: str, council: Council, tier, cancel) -> List[str]:
    """Both workers' replies, in the order the models are named, skipping any that failed.

    Concurrent because they are genuinely independent -- neither sees the other, which is the
    whole reason two of them are worth having -- so a council costs one worker's latency plus the
    reconciler's rather than three calls end to end.
    """
    def ask(model: str) -> Optional[str]:
        try:
            return call(complete, system, user,
                        tier=replace(tier, model=model, name=f"{tier.name}:worker"))
        except Exception as exc:
            logger.warning("Council worker %s did not answer: %s", model, exc)
            return None

    with ThreadPoolExecutor(max_workers=len(council.workers)) as pool:
        replies = list(pool.map(ask, council.workers))
    cancellation.check(cancel)
    return [reply for reply in replies if reply]


def deliberate_batch(complete: Callable[..., str], system: str, user_messages: Sequence[str], *,
                     stage: str, tier, cancel=None, on_progress=None,
                     **extra) -> List[Union[str, BaseException]]:
    """A council over a batched pass. Same contract as :func:`calling.call_batch`.

    Three flights rather than three calls per chunk. Each worker takes the *whole* batch in one
    flight, and the two flights overlap; the reconciler then takes one flight carrying every
    chunk with its two readings attached. So a council over a batched pass costs three flights
    however many chunks there are, and takes about twice the wall time of one -- the workers
    overlap with each other, and the reconciler waits for both.

    A chunk one worker failed on still reaches the reconciler with the other worker's reading. A
    chunk both failed on is passed through as the failure, which every caller of ``call_batch``
    already handles: :func:`calling.parsed_reply` reports it and leaves that chunk as the passes
    before it set it.
    """
    council = for_stage(stage)
    if council is None or not user_messages:
        return call_batch(complete, system, user_messages, tier=tier, cancel=cancel,
                          on_progress=on_progress, **extra)

    cancellation.check(cancel)
    total = len(user_messages)

    def flight(model: str) -> List[Union[str, BaseException]]:
        return call_batch(complete, system, user_messages,
                          tier=replace(tier, model=model, name=f"{tier.name}:worker"),
                          cancel=cancel, **extra)

    # The two workers overlap: they are answering the same prompts and neither sees the other.
    with ThreadPoolExecutor(max_workers=len(council.workers)) as pool:
        readings = list(pool.map(flight, council.workers))

    cancellation.check(cancel)
    reconciled, passthrough = [], {}
    for index, message in enumerate(user_messages):
        usable = [r[index] for r in readings
                  if index < len(r) and not isinstance(r[index], BaseException)]
        if not usable:
            # Both workers failed on this chunk. Sending it to the reconciler with nothing to
            # reconcile would be a plain single call dressed up as a council, and would report a
            # gateway failure as a reading.
            passthrough[index] = readings[0][index] if readings and index < len(readings[0]) \
                else RuntimeError("no council worker answered for this chunk")
            reconciled.append(None)
            continue
        reconciled.append(message + _RECONCILE_BLOCK.format(
            first=usable[0],
            second=usable[1] if len(usable) > 1 else "(this model did not answer)"))

    asking = [message for message in reconciled if message is not None]
    settled = call_batch(complete, system, asking,
                         tier=replace(tier, model=council.reconciler,
                                      name=f"{tier.name}:reconciler"),
                         cancel=cancel,
                         on_progress=(lambda done, _total: on_progress(done, total))
                         if on_progress else None,
                         **extra)

    out: List[Union[str, BaseException]] = []
    settling = iter(settled)
    for index in range(total):
        if index in passthrough:
            out.append(passthrough[index])
            continue
        reply = next(settling, None)
        # A reconciler that failed on a chunk two workers answered still has two answers in hand;
        # the first of them is a real reading and is better than reporting the chunk as lost.
        if reply is None or isinstance(reply, BaseException):
            usable = [r[index] for r in readings
                      if index < len(r) and not isinstance(r[index], BaseException)]
            logger.warning("The council's reconciler did not settle chunk %d of %s; using the "
                           "first reading instead.", index + 1, stage)
            out.append(usable[0] if usable else reply)
            continue
        out.append(reply)
    return out
