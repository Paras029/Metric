"""Three models on one judgement, where a second opinion is worth what it costs."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Sequence, Tuple, Union

from metric.llm import cancellation, config
from metric.llm.calling import call, call_batch

logger = logging.getLogger(__name__)

# The only passes a council can be turned on for, and the reason is in the module docstring: each
# is one judgement over a whole body of evidence that every later stage takes as given.
COUNCIL_STAGES = (
    # Reading the submitted documents. The first judgement of the run and the one everything else
    # inherits: the intake is drafted from this reading, the scenario space from that intake, and
    # nothing downstream ever re-reads the documents to check. A council was originally offered
    # only from the intake draft onwards, which was the wrong boundary -- by then the reading it
    # would be improving has already happened.
    "INGEST_READ", "INGEST_RESOLVE",
    "INGEST_DIAGRAM_READ", "INGEST_DIAGRAM_SYNTHESIZE", "INGEST_DIAGRAM_REPAIR",
    # Drafting the declaration from that reading, and reviewing what was built from it.
    "INTAKE_DRAFT", "INTAKE_REPAIR", "REVIEWER_ASSESS", "REVIEWER_PROPOSE",
    "COVERAGE_MAP",
)

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
    """The council configured for this call site, or ``None`` for the ordinary single call."""
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
               cancel=None, **extra) -> str:
    """Two workers in parallel, then a reconciler that answers with both in view."""
    council = for_stage(stage)
    if council is None:
        return call(complete, system, user, tier=tier, **extra)

    cancellation.check(cancel)
    readings = _workers(complete, system, user, council, tier, cancel, extra)
    if not readings:
        logger.warning("Neither council worker answered for %s; falling back to a single call.",
                       stage)
        return call(complete, system, user, tier=tier, **extra)

    cancellation.check(cancel)
    reconciled = user + _RECONCILE_BLOCK.format(
        first=readings[0],
        second=readings[1] if len(readings) > 1 else "(this model did not answer)")
    try:
        return call(complete, system, reconciled,
                    tier=replace(tier, model=council.reconciler,
                                 name=f"{tier.name}:reconciler"), **extra)
    except Exception as exc:
        logger.warning("The council's reconciler did not answer for %s (%s); using the first "
                       "reading instead.", stage, exc)
        return readings[0]


def _workers(complete, system: str, user: str, council: Council, tier, cancel,
             extra: Optional[dict] = None) -> List[str]:
    """Both workers' replies, in the order the models are named, skipping any that failed."""
    def ask(model: str) -> Optional[str]:
        try:
            return call(complete, system, user,
                        tier=replace(tier, model=model, name=f"{tier.name}:worker"),
                        **(extra or {}))
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
    """A council over a batched pass. Same contract as :func:`calling.call_batch`."""
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
