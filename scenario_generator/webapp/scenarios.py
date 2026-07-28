"""The benchmark as something a person can read on screen.

The registry holds everything: decision paths, per-turn expected outcomes, probe provenance,
signatures. Almost none of that belongs on a page. What a reviewer is doing here is deciding
whether a scenario is worth issuing, and that question is answered by the scenario's own words,
what it is judged to be worth, and whether anything has been flagged about it -- not by the route
it walks through the graph.

So this module deliberately narrows. The route, the seeded state, the expected outcome and the
internal identifiers stay in the workbook, which is where someone auditing the benchmark will
look for them. The screen carries the reading, and the workbook carries the record.

One consequence worth stating: the expected outcome is withheld here as well. It is not secret
from the validation team -- it is in their own registry -- but a page that puts the answer beside
the question is a page somebody eventually screenshots into an email to the modelling team.
"""
from __future__ import annotations

from typing import Dict, List

from ..core.generation import required_runs
from ..core.models import MATERIALITY, Scenario

# What each view narrows to. "Needs attention" is first because it is the reason to open this
# panel at all: a benchmark of three hundred scenarios is not read end to end, it is triaged.
VIEWS = (
    ("attention", "Needs attention"),
    ("high", "Critical and high"),
    ("all", "All scenarios"),
)

ORIGIN_LABELS = {"graph": "Route", "probe": "Probe", "llm-proposed": "Proposed"}

# Beyond this the page stops being a page. The rest stay one click away in the workbook, and the
# count of what is not shown is always stated rather than left for someone to discover.
PAGE_SIZE = 60


def _title(scenario: Scenario) -> str:
    """A one-line handle for the scenario, taken from its own first sentence."""
    text = (scenario.description or "").strip()
    if not text:
        return f"{ORIGIN_LABELS.get(scenario.origin, scenario.origin)} {scenario.id}"
    sentence = text.split(". ")[0].strip().rstrip(".")
    return sentence if len(sentence) <= 110 else sentence[:107].rstrip() + "…"


def _materiality_source(scenario: Scenario) -> str:
    """Where the effective tier came from. Three passes can set it and precedence is not obvious."""
    if scenario.materiality_override:
        return "your override"
    if scenario.review_materiality:
        return "the final review"
    return "the materiality pass"


def _reason(scenario: Scenario) -> str:
    """The rationale behind the tier that is actually in force."""
    if scenario.materiality_override:
        return ""
    if scenario.review_materiality:
        return scenario.review_rationale or scenario.materiality_rationale
    return scenario.materiality_rationale


def to_row(scenario: Scenario) -> Dict[str, object]:
    """One scenario as the page shows it."""
    return {
        "id": scenario.id,
        "origin": ORIGIN_LABELS.get(scenario.origin, scenario.origin),
        "is_probe": scenario.is_probe,
        "is_proposed": scenario.is_proposed,
        "title": _title(scenario),
        "description": scenario.description,
        "turn_plan": scenario.turn_plan,
        "persona": scenario.persona.name,
        "turns": scenario.turn_count,
        "category": scenario.category,
        "materiality": scenario.effective_materiality,
        "materiality_source": _materiality_source(scenario),
        "materiality_reason": _reason(scenario),
        "overridden": bool(scenario.materiality_override),
        "flag": scenario.review_flag,
        "flag_reason": scenario.review_rationale if scenario.review_flag else "",
        "coverage": scenario.owner_coverage,
        "coverage_note": scenario.owner_coverage_note,
        "runs": required_runs(scenario.effective_materiality),
        "proposal_reason": scenario.proposed_rationale,
    }


def needs_attention(row: Dict[str, object]) -> bool:
    """Whether this scenario is asking the reviewer for something.

    A flag is a recommendation waiting on a decision, a proposal is a scenario nothing enumerated,
    and a covered scenario is a candidate to drop. All three are things only a person can settle.
    """
    return bool(row["flag"] or row["is_proposed"] or row["coverage"])


def build_rows(scenarios: List[Scenario], view: str = "attention") -> Dict[str, object]:
    """The rows to show, the view that produced them, and what was left out.

    Returns the counts as well as the rows, because a filtered list that does not say what it
    filtered is a list that quietly loses scenarios.
    """
    ordering = {tier: index for index, tier in enumerate(reversed(MATERIALITY))}
    rows = sorted((to_row(s) for s in scenarios),
                  key=lambda r: (ordering.get(r["materiality"], len(MATERIALITY)), r["id"]))

    attention = [r for r in rows if needs_attention(r)]
    if view == "attention" and attention:
        selected = attention
    elif view == "high":
        selected = [r for r in rows if r["materiality"] in ("Critical", "High")]
    elif view == "attention":
        selected = rows                                    # nothing flagged; show the benchmark
        view = "all"
    else:
        selected = rows

    return {
        "rows": selected[:PAGE_SIZE],
        "view": view,
        "views": VIEWS,
        "shown": min(len(selected), PAGE_SIZE),
        "selected": len(selected),
        "total": len(rows),
        "attention": len(attention),
    }
