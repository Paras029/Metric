"""The scenario space as something a person can read on screen."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from metric.domain.models import required_variations
from metric.domain.models import MATERIALITY, ORIGIN_GRAPH, ORIGIN_PROBE, ORIGIN_PROPOSED, ORIGIN_VARIANT_GAP, Scenario

# Which columns a stage has actually produced by the time you are reading it, cumulative down
# the pipeline. The reason to scope this rather than always show everything is that most of these
# fields have a *default* -- every scenario carries "Medium" from the moment it is built -- and a
# tier shown on the scenario-text page reads as a judgement when it is only an unset field. The
# same goes for a variation count derived from it, and for review columns nothing has filled in yet.
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

ANY = "*"
"""What a row or column header selects: every tier of one category, or every category of one tier."""


def parse_cells(raw: Sequence[str]) -> List[Tuple[str, str]]:
    """``["Happy Path|High", "*|Low"]`` as (category, tier) pairs, deduplicated, in order given."""
    out: List[Tuple[str, str]] = []
    for item in raw or ():
        category, _, tier = str(item).partition("|")
        pair = (category.strip(), tier.strip())
        if any(pair) and pair not in out:
            out.append(pair)
    return out


def as_cell(category: str, tier: str) -> str:
    return f"{category}|{tier}"


def _matches(row: Dict[str, object], cells: Sequence[Tuple[str, str]]) -> bool:
    """Whether a scenario falls in any selected cell. No selection means the whole space."""
    if not cells:
        return True
    return any((category in (ANY, str(row["category"])))
               and (tier in (ANY, str(row["materiality"])))
               for category, tier in cells)


def _title(scenario: Scenario) -> str:
    """The handle a reader scans by: the scenario's own name where it has one."""
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
    """The rationale behind the tier that is actually in force."""
    if scenario.materiality_override:
        return ""
    if scenario.review_materiality:
        return scenario.review_rationale or scenario.materiality_rationale
    return scenario.materiality_rationale


def _flag_reason(scenario: Scenario) -> str:
    """What the flag adds beyond the reason already given for the tier, or nothing."""
    if not scenario.review_flag:
        return ""
    return "" if scenario.review_rationale == _reason(scenario) else scenario.review_rationale


def to_row(scenario: Scenario, capability_names: Optional[Dict[str, str]] = None
           ) -> Dict[str, object]:
    """One scenario as the page shows it."""
    named = (capability_names or {}).get(scenario.capability_id, "")
    # A scenario with no block is one of two things, and they read very differently. Either the
    # declaration draws no blocks at all -- in which case every scenario runs whole and saying so
    # on each is noise -- or this one crosses them, which is the most interesting thing about it:
    # the walk cannot produce those, so every one is a route somebody proposed on purpose.
    crosses = not scenario.capability_id and len(scenario.capabilities) > 1
    return {
        "id": scenario.id,
        "capability_id": scenario.capability_id,
        "capability": named or scenario.capability_id or ("End to end" if crosses else ""),
        "crosses_blocks": crosses,
        "capabilities": list(scenario.capabilities),
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


# Which block of the list a scenario belongs to. Three rather than two: a scenario the review
# proposed is a route nobody enumerated, and it reads as an addition to the routes rather than as
# one of them, so it sits between the walked ones and the probes.
_GROUPS = {ORIGIN_GRAPH: 0, ORIGIN_VARIANT_GAP: 0, ORIGIN_PROPOSED: 1, ORIGIN_PROBE: 2}


def _group_of(row: Dict[str, object]) -> int:
    if row.get("is_probe"):
        return 2
    if row.get("is_proposed"):
        return 1
    return 0


def needs_attention(row: Dict[str, object]) -> bool:
    """Whether this scenario is asking the reviewer for something."""
    return bool(row["flag"] or row["is_proposed"] or row["coverage"] or row["category_changed"])


def shape(scenarios: List[Scenario], stage: str = "summary",
          capability_names: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """How the scenario space is distributed, for the side panel."""
    shows = set(STAGE_COLUMNS.get(stage, STAGE_COLUMNS["summary"]))
    rows = [to_row(s, capability_names) for s in scenarios]
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


# How dark a cell in the matrix gets, as five steps rather than a continuous ramp. Discrete steps
# are what makes an unequal distribution legible: a reader comparing two continuously-shaded cells
# is guessing, where five steps can be told apart at a glance and counted off against each other.
_DENSITY_STEPS = 4
_FLAT_DENSITY = 2


def matrix(rows: List[Dict[str, object]],
           cells: Sequence[Tuple[str, str]] = ()) -> Dict[str, object]:
    """The scenario space as a grid: what kind of situation, against what it is worth."""
    chosen = list(cells or ())
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
                "active": (category, tier) in chosen,
                "toggle": _toggled(chosen, (category, tier)),
            })
        grid.append({"category": category, "cells": cells,
                     "total": sum(cell["count"] for cell in cells),
                     "active": (category, ANY) in chosen,
                     "toggle": _toggled(chosen, (category, ANY))})
    return {
        "tiers": [{"tier": tier,
                   "count": sum(counts.get((c, tier), 0) for c in categories),
                   "active": (ANY, tier) in chosen,
                   "toggle": _toggled(chosen, (ANY, tier))}
                  for tier in tiers],
        "rows": grid,
        "total": len(rows),
        "empty": empty,
        "chosen": [{"category": c, "tier": t, "label": _cell_label(c, t),
                    "toggle": _toggled(chosen, (c, t))} for c, t in chosen],
    }


def _toggled(chosen: Sequence[Tuple[str, str]], cell: Tuple[str, str]) -> List[str]:
    """The selection that clicking ``cell`` would produce: added if absent, removed if present."""
    kept = [c for c in chosen if c != cell]
    if len(kept) == len(chosen):
        kept = list(chosen) + [cell]
    return [as_cell(category, tier) for category, tier in kept]


def _cell_label(category: str, tier: str) -> str:
    if category == ANY:
        return f"{tier}, every category"
    if tier == ANY:
        return f"{category}, every tier"
    return f"{category} · {tier}"



def build_rows(scenarios: List[Scenario], stage: str = "summary",
               cells: Sequence[Tuple[str, str]] = (), limit: int = PAGE_SIZE,
               capability_names: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """The rows to show, the grid that selects them, and what was left out."""
    shows = set(STAGE_COLUMNS.get(stage, STAGE_COLUMNS["summary"]))
    rows = [to_row(s, capability_names) for s in scenarios]

    # Functional scenarios first, probes after them. A probe is path-independent -- it tests what
    # the agent must refuse whatever route it is on -- so it belongs in the pack but not at the top
    # of it: the routes through the declared graph are what the exercise is about, and a list
    # opening with forty probes buries them. Ids sort probes first on their own (NF- before SC-),
    # which is how they came to lead.
    if MATERIALITY_COLUMNS in shows:
        ordering = {tier: index for index, tier in enumerate(reversed(MATERIALITY))}
        rows.sort(key=lambda r: (_group_of(r), ordering.get(r["materiality"], len(MATERIALITY)),
                                 r["id"]))
    else:
        rows.sort(key=lambda r: (_group_of(r), r["id"]))

    chosen = list(cells or ()) if MATERIALITY_COLUMNS in shows else []
    grid = matrix(rows, chosen) if MATERIALITY_COLUMNS in shows else {}
    selected = [r for r in rows if _matches(r, chosen)]
    shown_rows = selected if limit is None else selected[:limit]

    return {
        "rows": shown_rows,
        "shows": shows,
        "matrix": grid,
        "cells": [as_cell(category, tier) for category, tier in chosen],
        "limit": limit,
        "limit_options": PAGE_SIZE_OPTIONS,
        "shown": len(shown_rows),
        "selected": len(selected),
        "total": len(rows),
        "attention": sum(1 for r in rows if needs_attention(r)),
    }
