"""Running a pass over part of the benchmark without letting it forget the rest.

Re-running a whole pass to fix a handful of scenarios is expensive and, on a benchmark of three
hundred, slow enough that people stop doing it -- so a tier nobody agrees with stays where it is.
Every judging pass therefore takes an optional set of ids and works on those alone.

What it must not do is judge them *in isolation*. All three passes are comparative, and each in a
different way: materiality asks whether this scenario is the worst thing here, the review asks
what the whole set already covers, and both read the deterministic redundancy signals, which are
by definition a statement about a scenario's peers. Narrowing the set those are computed from
would silently change the answer -- five scenarios reviewed on their own would each look
uniquely important, because there would be nothing else in view to be more important than.

So the split is always the same: **the whole benchmark for context, the chosen scenarios for
work**. The digest, the peer signals and the totals are built from everything; only the chunks
sent for judgement are narrowed. A subset run and a full run therefore give the same verdict for
the same scenario, which is the property that makes re-running part of the benchmark trustworthy
at all.
"""
from __future__ import annotations

from typing import Iterable, List, Optional


def narrow(scenarios: List, only: Optional[Iterable[str]]) -> List:
    """The scenarios a pass will actually work on.

    ``only`` is a set of scenario ids, or None for the whole benchmark. Ids that match nothing are
    ignored rather than raising: the selection comes from a page that may have been open while
    another stage rewrote the registry, and a stale id is a scenario that no longer exists rather
    than a fault worth stopping a run for. Order follows the benchmark, not the selection, so the
    chunks a subset run sends are the chunks a full run would have sent minus the rest.
    """
    if only is None:
        return list(scenarios)
    wanted = set(only)
    return [scenario for scenario in scenarios if scenario.id in wanted]
