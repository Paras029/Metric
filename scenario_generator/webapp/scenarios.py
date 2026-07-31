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

# Which columns a stage has actually produced by the time you are reading it, cumulative down
# the pipeline. The reason to scope this rather than always show everything is that most of these
# fields have a *default* -- every scenario carries "Medium" from the moment it is built -- and a
# tier shown on the scenario-text page reads as a judgement when it is only an unset field. The
# same goes for a run count derived from it, and for review columns nothing has filled in yet.
#
# Going back to an earlier stage therefore shows that stage's own reading rather than the current
# state of the registry, which is the other half of the same idea: what you are looking at is what
# that stage produced, not what later stages have since made of it.
TEXT, MATERIALITY_COLUMNS, REVIEW, COVERAGE = "text", "materiality", "review", "coverage"

STAGE_COLUMNS = {
    "text": (TEXT,),
    "materiality": (TEXT, MATERIALITY_COLUMNS),
    "review": (TEXT, MATERIALITY_COLUMNS, REVIEW),
    "coverage": (TEXT, MATERIALITY_COLUMNS, REVIEW, COVERAGE),
    "issue": (TEXT, MATERIALITY_COLUMNS, REVIEW, COVERAGE),
}

ORIGIN_LABELS = {"graph": "Route", "probe": "Probe", "llm-proposed": "Proposed"}

# Beyond this the page stops being a page. The rest stay one click away in the workbook, and the
# count of what is not shown is always stated rather than left for someone to discover. A person
# can ask for more -- see PAGE_SIZE_OPTIONS -- so this is a starting point, not a hard cap.
PAGE_SIZE = 50
PAGE_SIZE_OPTIONS = (50, 100, 250, "all")

# Columns a person can narrow the list to, each mapped to the row field it reads and, where the
# vocabulary is closed, the values worth offering even before any scenario has one -- so the
# dropdown does not visibly change shape as a benchmark moves through materiality and review.
# Persona and flag are left off this map (open-ended or benchmark-specific) and are populated
# from whatever the rows actually contain instead.
FILTER_FIELDS: Dict[str, str] = {
    "category": "category", "materiality": "materiality", "origin": "origin",
    "persona": "persona", "flag": "flag", "coverage": "coverage",
}
_CLOSED_FILTER_VALUES = {
    "materiality": tuple(reversed(MATERIALITY)),
    "origin": tuple(ORIGIN_LABELS.values()),
}
# Only offered once a stage has actually produced the column -- see STAGE_COLUMNS above.
_FILTER_REQUIRES = {"materiality": MATERIALITY_COLUMNS, "flag": REVIEW, "coverage": COVERAGE}


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
        "category": scenario.effective_category,
        "category_changed": bool(scenario.review_category),
        "category_reason": scenario.review_category_rationale,
        "declared_category": scenario.category,
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
    a re-read category is a declaration that looks wrong, and a covered scenario is a candidate to
    drop. All four are things only a person can settle.
    """
    return bool(row["flag"] or row["is_proposed"] or row["coverage"] or row["category_changed"])


def _filter_options(rows: List[Dict[str, object]], shows: set) -> Dict[str, List[str]]:
    """Distinct values worth offering per filterable column.

    Scoped twice over: to the columns this stage has actually produced (the same reasoning as
    :data:`STAGE_COLUMNS` -- a coverage filter on the scenario-text page would offer to filter a
    column that does not exist yet), and to values with more than one option, since a dropdown
    that can only ever narrow to everything is not a filter.
    """
    options: Dict[str, List[str]] = {}
    for field, row_key in FILTER_FIELDS.items():
        requires = _FILTER_REQUIRES.get(field)
        if requires and requires not in shows:
            continue
        present = {str(r[row_key]) for r in rows if r.get(row_key)}
        if field in _CLOSED_FILTER_VALUES:
            values = [v for v in _CLOSED_FILTER_VALUES[field] if v in present]
        else:
            values = sorted(present)
        if len(values) > 1:
            options[field] = values
    return options


def build_rows(scenarios: List[Scenario], view: str = "attention", stage: str = "issue",
               filters: Dict[str, str] = None, limit: int = PAGE_SIZE) -> Dict[str, object]:
    """The rows to show, the view and filters that produced them, and what was left out.

    Returns the counts as well as the rows, because a filtered list that does not say what it
    filtered is a list that quietly loses scenarios, and the set of columns this stage has
    produced -- see :data:`STAGE_COLUMNS` for why that is scoped rather than always complete.

    ``filters`` narrows within the view rather than replacing it -- picking "Critical and high"
    and then a persona shows critical-and-high scenarios for that persona, not one or the other.
    ``limit`` caps how many of the narrowed set are actually rendered; ``None`` renders all of
    them, for the person who has decided fifty is not enough for this benchmark.
    """
    shows = set(STAGE_COLUMNS.get(stage, STAGE_COLUMNS["issue"]))
    rows = [to_row(s) for s in scenarios]

    # Ordering by tier only says something once a tier has been assigned. Before that every
    # scenario carries the same default and the sort is an illusion of ranking, so they stay in
    # the order the benchmark built them.
    if MATERIALITY_COLUMNS in shows:
        ordering = {tier: index for index, tier in enumerate(reversed(MATERIALITY))}
        rows.sort(key=lambda r: (ordering.get(r["materiality"], len(MATERIALITY)), r["id"]))
    else:
        rows.sort(key=lambda r: r["id"])

    views = [v for v in VIEWS if v[0] == "all" or MATERIALITY_COLUMNS in shows]
    if view not in {key for key, _ in views}:
        view = views[0][0]

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

    # Options are read off the view, before the filters narrow it further -- a person choosing
    # between personas should see every persona the current view has, not only the one they
    # already picked, which is what reading the options after filtering would leave them with.
    filter_options = _filter_options(selected, shows)

    active = {field: value for field, value in (filters or {}).items()
             if value and field in FILTER_FIELDS}
    for field, value in active.items():
        row_key = FILTER_FIELDS[field]
        selected = [r for r in selected if str(r.get(row_key)) == value]

    shown_rows = selected if limit is None else selected[:limit]

    return {
        "rows": shown_rows,
        "view": view,
        "views": tuple(views),
        "shows": shows,
        "filter_options": filter_options,
        "active_filters": active,
        "limit": limit,
        "limit_options": PAGE_SIZE_OPTIONS,
        "shown": len(shown_rows),
        "selected": len(selected),
        "total": len(rows),
        "attention": len(attention),
    }
