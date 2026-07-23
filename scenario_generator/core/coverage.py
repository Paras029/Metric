"""Match an agent owner's own scenarios against the generated benchmark.

Decision path and persona are a combined hard gate — an owner scenario is only a candidate
match if BOTH align exactly with a benchmark scenario:

    exact path + exact persona   -> candidate (goes on to the metadata check below)
    exact path + persona differs  -> No match, full stop
    path matches nothing          -> No match, full stop

Only once a scenario clears that gate does any OTHER extracted metadata (category,
capabilities) come into play, and only to demote — never to create a match:

    gate passed + other metadata consistent -> Match
    gate passed + other metadata disagrees  -> Partial match

Every disagreement is recorded in `Match.notes`, including a persona mismatch that fails the
gate, so a reviewer always sees why a scenario landed where it did.

Separately, each owner scenario's extraction carries a binary confidence (Confident /
Watch-out), reported as its own column so a reviewer can weigh how much to trust the verdict.
"""
from __future__ import annotations

from collections import Counter
from typing import List, Tuple

from .models import BenchmarkScenario, ExtractedMeta, Match, OwnerScenario


def match_scenarios(owner_scenarios: List[OwnerScenario], extracted: List[ExtractedMeta],
                    benchmark: List[BenchmarkScenario], default_persona_id: str) -> List[Match]:
    """Judge each owner scenario against the benchmark: path + persona gate, other metadata demotes."""
    by_signature = {b.signature: b for b in benchmark}
    matches = []
    for owner, meta in zip(owner_scenarios, extracted):
        match = Match(owner=owner, extracted=meta)
        target = by_signature.get(meta.signature) if meta.decision_path else None

        if target is not None:
            owner_persona = meta.persona_id or default_persona_id
            if owner_persona != target.persona_id:
                match.notes = (f"path matches {target.id} but persona differs (owner: "
                               f"{owner_persona or 'unspecified'}, benchmark: {target.persona_id}) "
                               "— treated as no match")
            else:
                match.scenario_id = target.id
                flags = []
                if meta.category and target.category and meta.category != target.category:
                    flags.append(f"category differs (owner: {meta.category}, benchmark: {target.category})")
                if meta.capabilities and target.capabilities and \
                        set(meta.capabilities) != set(target.capabilities):
                    flags.append("capabilities differ")
                match.verdict = "Partial match" if flags else "Match"
                match.notes = "; ".join(flags)
        matches.append(match)
    return matches


def coverage_gaps(matches: List[Match], benchmark: List[BenchmarkScenario]) -> List[BenchmarkScenario]:
    """Benchmark scenarios no owner scenario matched (Match or Partial match)."""
    covered = {m.scenario_id for m in matches if m.scenario_id}
    return [b for b in benchmark if b.id not in covered]


def incremental_owner(matches: List[Match]) -> List[Match]:
    """Owner scenarios that are not a match (path/persona gate failed, or path matched nothing)."""
    return [m for m in matches if m.verdict == "No match"]


def coverage_summary(matches: List[Match],
                     benchmark: List[BenchmarkScenario]) -> List[Tuple[str, str]]:
    gaps = coverage_gaps(matches, benchmark)
    covered = len(benchmark) - len(gaps)
    gaps_by_materiality = Counter(b.materiality for b in gaps)
    grid = Counter((m.verdict, m.extracted.confidence) for m in matches)

    pct = f"{covered}/{len(benchmark)} ({100 * covered // len(benchmark) if benchmark else 0}%)"
    rows = [
        ("Benchmark scenarios", str(len(benchmark))),
        ("Benchmark scenarios covered", pct),
        ("Coverage gaps (never tested)", str(len(gaps))),
        ("Coverage gaps by materiality",
         ", ".join(f"{k}: {v}" for k, v in gaps_by_materiality.items()) or "-"),
        ("Owner scenarios read", str(len(matches))),
    ]
    for verdict in ("Match", "Partial match", "No match"):
        for confidence in ("Confident", "Watch-out"):
            count = grid.get((verdict, confidence), 0)
            if count:
                rows.append((f"  {verdict} / {confidence}", str(count)))
    return rows
