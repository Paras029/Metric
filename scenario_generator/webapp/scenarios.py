"""The scenario space as something a person can read on screen.

The scenario space metadata holds everything: decision paths, per-turn expected outcomes, probe provenance,
signatures. Almost none of that belongs on a page. What a reviewer is doing here is deciding
whether a scenario is worth issuing, and that question is answered by the scenario's own words,
what it is judged to be worth, and whether anything has been flagged about it -- not by the route
it walks through the graph.

So this module deliberately narrows. The route, the seeded state, the expected outcome and the
internal identifiers stay in the workbook, which is where someone auditing the scenario space will
look for them. The screen carries the reading, and the workbook carries the record.

One consequence worth stating: the expected outcome is withheld here as well. It is not secret
from the validator -- it is in their own metadata workbook -- but a page that puts the answer beside
the question is a page somebody eventually screenshots into an email to the model owner.
"""
from __future__ import annotations

from typing import Dict, List

from ..core.generation import required_variations
from ..core.models import (MATERIALITY, ORIGIN_GRAPH, ORIGIN_PROBE, ORIGIN_PROPOSED,
                           ORIGIN_VARIANT_GAP, Scenario)

# What each view narrows to. "Needs attention" is first because it is the reason to open this
# panel at all: a space of three hundred scenarios is not read end to end, it is triaged.
VIEWS = (
    ("attention", "Needs attention"),
    ("high", "Critical and high"),
    ("all", "All scenarios"),
)

# Which columns a stage has actually produced by the time you are reading it, cumulative down
# the pipeline. The reason to scope this rather than always show everything is that most of these
# fields have a *default* -- every scenario carries "Medium" from the moment it is built -- and a
# tier shown on the scenario-text page reads as a judgement when it is only an unset field. The
# same goes for a variation count derived from it, and for review columns nothing has filled in yet.
#
# Going back to an earlier stage therefore shows that stage's own reading rather than the current
# state of the scenario space metadata, which is the other half of the same idea: what you are looking at is what
# that stage produced, not what later stages have since made of it.
TEXT, MATERIALITY_COLUMNS, REVIEW, COVERAGE = "text", "materiality", "review", "coverage"

STAGE_COLUMNS = {
    "scenarios": (TEXT,),
    "variations": (TEXT,),
    "materiality": (TEXT, MATERIALITY_COLUMNS),
    "review": (TEXT, MATERIALITY_COLUMNS, REVIEW),
    "coverage": (TEXT, MATERIALITY_COLUMNS, REVIEW, COVERAGE),
    "summary": (TEXT, MATERIALITY_COLUMNS, REVIEW, COVERAGE),
}

# Every origin gets a label, and the map must stay exhaustive: a scenario whose origin is missing
# here shows its raw slug on the page, and can never be selected in the origin filter, which is
# built from these values.
ORIGIN_LABELS = {
    ORIGIN_GRAPH: "Route",
    ORIGIN_VARIANT_GAP: "Added route",
    ORIGIN_PROBE: "Probe",
    ORIGIN_PROPOSED: "Proposed",
}

# Beyond this the page stops being a page. The rest stay one click away in the workbook, and the
# count of what is not shown is always stated rather than left for someone to discover. A person
# can ask for more -- see PAGE_SIZE_OPTIONS -- so this is a starting point, not a hard cap.
PAGE_SIZE = 50
PAGE_SIZE_OPTIONS = (50, 100, 250, "all")

# Columns a person can narrow the list to, each mapped to the row field it reads and, where the
# vocabulary is closed, the values worth offering even before any scenario has one -- so the
# dropdown does not visibly change shape as a scenario space moves through materiality and review.
# Persona and flag are left off this map (open-ended or scenario space-specific) and are populated
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
    """The handle a reader scans by: the scenario's own name where it has one.

    The name is written for exactly this -- short, distinguishable from its neighbours, and about
    the situation rather than the expected behaviour. Falling back to the first sentence of the
    description is what a scenario space written before names existed has, and it is a worse handle
    for the reason the name exists: three hundred first sentences that all begin "The cardmember"
    are not scannable.
    """
    name = (scenario.name or "").strip()
    if name:
        return name
    text = (scenario.description or "").strip()
    if not text:
        return f"{ORIGIN_LABELS.get(scenario.origin, scenario.origin)} {scenario.id}"
    sentence = text.split(". ")[0].strip().rstrip(".")
    if len(sentence) <= 110:
        return sentence
    # Cut at the last word boundary rather than at the character. "files the dispute wi…" reads
    # as a rendering fault; "files the dispute…" reads as a summary.
    return sentence[:107].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _reason(scenario: Scenario) -> str:
    """The rationale behind the tier that is actually in force.

    Which pass produced it is deliberately not carried alongside. A reviewer reading a card wants
    to know why this scenario is Critical, and "from the materiality pass" answers a question
    nobody asked while pushing the answer they did ask for further down the card. The workbook
    keeps every pass's column separately for anyone auditing how the tier was reached.
    """
    if scenario.materiality_override:
        return ""
    if scenario.review_materiality:
        return scenario.review_rationale or scenario.materiality_rationale
    return scenario.materiality_rationale


def _flag_reason(scenario: Scenario) -> str:
    """What the flag adds beyond the reason already given for the tier, or nothing.

    The review settles the tier and raises the flag in one reading and returns one rationale for
    both, so on a flagged scenario the two are usually the same sentence. Printed twice it reads
    as a rendering fault rather than as agreement, so the flag is folded onto the tier's own note
    -- see ``flag_shares_reason`` -- and only gets a note of its own where it genuinely says
    something the tier's does not.
    """
    if not scenario.review_flag:
        return ""
    return "" if scenario.review_rationale == _reason(scenario) else scenario.review_rationale


def to_row(scenario: Scenario) -> Dict[str, object]:
    """One scenario as the page shows it."""
    return {
        "id": scenario.id,
        "origin": ORIGIN_LABELS.get(scenario.origin, scenario.origin),
        "is_probe": scenario.is_probe,
        "is_proposed": scenario.is_proposed,
        "title": _title(scenario),
        "name": scenario.name,
        "description": scenario.description,
        "turn_plan": scenario.turn_plan,
        "persona": scenario.persona.name,
        "turns": scenario.turn_count,
        "category": scenario.effective_category,
        "category_changed": bool(scenario.review_category),
        "category_reason": scenario.review_category_rationale,
        "declared_category": scenario.category,
        "materiality": scenario.effective_materiality,
        "materiality_reason": _reason(scenario),
        "overridden": bool(scenario.materiality_override),
        "flag": scenario.review_flag,
        "flag_reason": _flag_reason(scenario),
        "flag_shares_reason": bool(scenario.review_flag) and not _flag_reason(scenario),
        "coverage": scenario.owner_coverage,
        "coverage_note": scenario.owner_coverage_note,
        "runs": required_variations(scenario.effective_materiality),
        "proposal_reason": scenario.proposed_rationale,
    }


def needs_attention(row: Dict[str, object]) -> bool:
    """Whether this scenario is asking the reviewer for something.

    A flag is a recommendation waiting on a decision, a proposal is a scenario nothing enumerated,
    a re-read category is a declaration that looks wrong, and a covered scenario is a candidate to
    drop. All four are things only a person can settle.
    """
    return bool(row["flag"] or row["is_proposed"] or row["coverage"] or row["category_changed"])


def shape(scenarios: List[Scenario], stage: str = "summary") -> Dict[str, object]:
    """How the scenario space is distributed, for the side panel.

    Counted over every scenario rather than over the rows on screen. The list in the middle of
    the page is a view -- narrowed to what needs attention, filtered, and cut off at a page
    length -- and a tally taken from it would answer "what am I looking at" when the question the
    panel exists to answer is "what is in the scenario space".

    Tiers appear only once the stage has actually produced them, for the reason given on
    :data:`STAGE_COLUMNS`: every scenario carries Medium from the moment it is built, and a
    distribution drawn before the materiality pass would be a chart of a default. From the review
    stage onwards the tiers here are the effective ones, so a tier the review moved is counted
    where the review put it.
    """
    shows = set(STAGE_COLUMNS.get(stage, STAGE_COLUMNS["summary"]))
    rows = [to_row(s) for s in scenarios]
    tiers = []
    if MATERIALITY_COLUMNS in shows:
        for tier in reversed(MATERIALITY):
            at_tier = [r for r in rows if r["materiality"] == tier]
            if at_tier:
                tiers.append({"tier": tier, "count": len(at_tier),
                              "runs": sum(r["runs"] for r in at_tier)})
    return {
        "total": len(rows),
        "tiers": tiers,
        "runs": sum(r["runs"] for r in rows) if MATERIALITY_COLUMNS in shows else 0,
        "attention": sum(1 for r in rows if needs_attention(r)),
        "flagged": sum(1 for r in rows if r["flag"]) if REVIEW in shows else 0,
        "proposed": sum(1 for r in rows if r["is_proposed"]) if REVIEW in shows else 0,
    }


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


# How dark a cell in the matrix gets, as five steps rather than a continuous ramp. Discrete steps
# are what makes an unequal distribution legible: a reader comparing two continuously-shaded cells
# is guessing, where five steps can be told apart at a glance and counted off against each other.
_DENSITY_STEPS = 4
_FLAT_DENSITY = 2


def matrix(rows: List[Dict[str, object]], active: Dict[str, str] = None) -> Dict[str, object]:
    """The scenario space as a grid: what kind of situation, against what it is worth.

    A list of three hundred scenarios sorted by tier answers "what is most material" and nothing
    else. The two questions actually being asked at this point in a validation are *where is the
    mass* -- is this a scenario space of eighty happy paths and four terminations? -- and, more
    sharply, *which cells are empty*. An empty Critical/Termination cell is a hole in the exercise,
    and no ranked list will ever show it, because a hole has no row.

    Counted over the rows handed in, which is the current view before its filters are applied, so
    every cell's count is exactly what clicking that cell will show.
    """
    active = active or {}
    tiers = [t for t in reversed(MATERIALITY) if any(r["materiality"] == t for r in rows)]
    categories = sorted({str(r["category"]) for r in rows if r.get("category")})
    if not tiers or not categories:
        return {}

    counts = {(str(r["category"]), str(r["materiality"])): 0 for r in rows}
    for row in rows:
        key = (str(row["category"]), str(row["materiality"]))
        counts[key] = counts.get(key, 0) + 1
    filled = [count for count in counts.values() if count]
    highest = max(filled) if filled else 0
    # A grid where every occupied cell holds the same number has no distribution to draw, and
    # scaling it against its own maximum paints all of them at full strength -- which reads as
    # "everything is dense" when it means "nothing here varies". One middling step for all of
    # them says the true thing, and leaves the empty cells as the only contrast, which is what
    # such a grid is actually showing.
    flat = highest and min(filled) == highest

    grid, empty = [], 0
    for category in categories:
        cells = []
        for tier in tiers:
            count = counts.get((category, tier), 0)
            empty += not count
            cells.append({
                "tier": tier,
                "category": category,
                "count": count,
                # Ceiling rather than rounding: a cell holding one scenario must never shade as
                # empty, which is the one distinction this grid exists to make.
                "density": (0 if not count else _FLAT_DENSITY if flat
                            else -(-count * _DENSITY_STEPS // highest)),
                "active": (active.get("category") == category
                           and active.get("materiality") == tier),
            })
        grid.append({"category": category, "cells": cells,
                     "total": sum(cell["count"] for cell in cells),
                     "active": active.get("category") == category
                     and not active.get("materiality")})
    return {
        "tiers": [{"tier": tier,
                   "count": sum(counts.get((c, tier), 0) for c in categories),
                   "active": active.get("materiality") == tier and not active.get("category")}
                  for tier in tiers],
        "rows": grid,
        "total": len(rows),
        "empty": empty,
    }


def build_rows(scenarios: List[Scenario], view: str = "attention", stage: str = "summary",
               filters: Dict[str, str] = None, limit: int = PAGE_SIZE) -> Dict[str, object]:
    """The rows to show, the view and filters that produced them, and what was left out.

    Returns the counts as well as the rows, because a filtered list that does not say what it
    filtered is a list that quietly loses scenarios, and the set of columns this stage has
    produced -- see :data:`STAGE_COLUMNS` for why that is scoped rather than always complete.

    ``filters`` narrows within the view rather than replacing it -- picking "Critical and high"
    and then a persona shows critical-and-high scenarios for that persona, not one or the other.
    ``limit`` caps how many of the narrowed set are actually rendered; ``None`` renders all of
    them, for the person who has decided fifty is not enough for this scenario space.
    """
    shows = set(STAGE_COLUMNS.get(stage, STAGE_COLUMNS["summary"]))
    rows = [to_row(s) for s in scenarios]

    # Ordering by tier only says something once a tier has been assigned. Before that every
    # scenario carries the same default and the sort is an illusion of ranking, so they stay in
    # the order the scenario space built them.
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
        selected = rows                                    # nothing flagged; show the scenario space
        view = "all"
    else:
        selected = rows

    # Options are read off the view, before the filters narrow it further -- a person choosing
    # between personas should see every persona the current view has, not only the one they
    # already picked, which is what reading the options after filtering would leave them with.
    filter_options = _filter_options(selected, shows)

    active = {field: value for field, value in (filters or {}).items()
             if value and field in FILTER_FIELDS}
    # Built from the view before the filters run, for the same reason as the options above: a grid
    # rebuilt after filtering shows one cell holding everything, which is a picture of the filter
    # rather than of the scenario space.
    grid = matrix(selected, active) if MATERIALITY_COLUMNS in shows else {}
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
        "matrix": grid,
        "active_filters": active,
        "limit": limit,
        "limit_options": PAGE_SIZE_OPTIONS,
        "shown": len(shown_rows),
        "selected": len(selected),
        "total": len(rows),
        "attention": len(attention),
    }
