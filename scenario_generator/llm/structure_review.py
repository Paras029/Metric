"""One call that looks at the whole declared graph and proposes how to make it sounder before it
is walked into a scenario space.

Two different problems, both caught here because both are cheapest to fix before the scenario space
exists rather than after:

    reconnections   a decision or state that is declared but does not connect to the rest of the
                    graph -- nothing leads to it, or it leads nowhere.
    consolidations  two or more decisions that are alternate ways of establishing the same fact
                    rather than genuinely different branches. Separately, they multiply the
                    scenario space by every combination without testing anything additional past the
                    point where they converge; merged into one decision, the same downstream
                    behaviour is tested at a fraction of the cost.

Deliberately one call. The declared graph is a small fraction of the size of the scenario space it
produces, so the whole of it fits in one prompt, and a pass that only ever proposes -- never
writes -- is the one place a structural judgement this consequential is allowed to be wrong
without cost: every proposal is applied by a person, one at a time, from the intake stage. See
:mod:`scenario_generator.core.intake` for what applying one actually does to the workbook.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..core.graph import DecisionGraph, convergence_candidates
from ..core.models import IntakeData
from . import config, prompt_loader
from .calling import call
from .context import describe_graph, describe_use_case, supplementary_context
from .gateway import ask_llm
from ..utils import parse_json_object
from ..utils.replies import prose

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "structure_review.system"
_TASK_PROMPT = "structure_review.task"

CompletionFn = Callable[..., str]


@dataclass
class Reconnection:
    """One orphaned or misrouted piece of the graph, with a mechanical fix.

    ``kind`` decides which fix applies: a ``"decision"`` is attached to a state that should lead
    to it, a ``"state"`` has its own Reached Via corrected. Never both -- the two are different
    edits to different columns.
    """

    kind: str
    id: str
    attach_to_state: str = ""
    reached_via: str = ""
    rationale: str = ""


@dataclass
class Consolidation:
    """A proposal to collapse several decisions that are alternate routes to the same fact."""

    decisions: List[str] = field(default_factory=list)
    id: str = ""
    name: str = ""
    outcomes: List[str] = field(default_factory=list)
    outcome_map: Dict[str, str] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    importance: str = ""
    rationale: str = ""


@dataclass
class StructureReview:
    reconnections: List[Reconnection] = field(default_factory=list)
    consolidations: List[Consolidation] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.reconnections or self.consolidations)


def _render_hints(intake: IntakeData) -> str:
    """Structural merge candidates, computed rather than judged -- see the module docstring."""
    graph = DecisionGraph(intake.decisions, intake.states)
    groups = convergence_candidates(graph)
    if not groups:
        return ("None found. Any consolidation you propose has to be argued from the graph and "
                "the use case alone, with no structural convergence to lean on.")
    names = {d.id: d.name for d in intake.decisions}
    return "\n".join(
        f"- {', '.join(f'{i} ({names.get(i, i)})' for i in group)} -- every outcome of each "
        f"lands on the same downstream point(s) as every outcome of the others"
        for group in groups)


def _validate_reconnections(entries, decision_ids, state_ids) -> List[Reconnection]:
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        identifier = str(entry.get("id", "")).strip().upper()
        rationale = prose(entry, "rationale")

        if kind == "decision" and identifier in decision_ids:
            target = str(entry.get("attach_to_state", "")).strip().upper()
            if target in state_ids:
                out.append(Reconnection(kind="decision", id=identifier, attach_to_state=target,
                                        rationale=rationale))
        elif kind == "state" and identifier in state_ids:
            reached_via = str(entry.get("reached_via", "")).strip()
            if reached_via:
                out.append(Reconnection(kind="state", id=identifier, reached_via=reached_via,
                                        rationale=rationale))
    return out


def _validate_consolidations(entries, decision_ids, block_of=None) -> List[Consolidation]:
    out = []
    block_of = block_of or {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        members = list(dict.fromkeys(
            str(d).strip().upper() for d in (entry.get("decisions") or [])
            if str(d).strip().upper() in decision_ids))
        kept = str(entry.get("id", "")).strip().upper()
        if len(members) < 2 or kept not in members:
            continue

        # Refused rather than requested, but only between capabilities that are actually blocks.
        #
        # The distinction matters, because "capability" means two different things in an intake
        # depending on whether a span has been drawn on it. Without one it is a label -- and two
        # decisions labelled "identify via last four" and "identify via full SSN" are very often
        # exactly the same check split in two, which is the single most useful merge this pass
        # proposes. With a span it is a block of the graph with its own entry and exit, and
        # merging across one welds two blocks together and re-enumerates the whole scenario
        # space: far more than the proposal claims to do, and invisible in a reply that otherwise
        # reads perfectly well, when applying it is a click.
        blocks = {block_of.get(member, "") for member in members}
        if len(blocks) > 1 and any(block for block in blocks):
            logger.info("Discarded a proposed merge of %s: %s are separate blocks of the graph "
                        "with their own spans, and merging across one re-enumerates the whole "
                        "scenario space.", ", ".join(members),
                        " and ".join(sorted(b for b in blocks if b)))
            continue

        outcomes = [str(o).strip() for o in (entry.get("outcomes") or []) if str(o).strip()]
        if len(outcomes) < 2:
            continue

        raw_map = entry.get("outcome_map") or {}
        outcome_map = {str(k).strip(): str(v).strip() for k, v in raw_map.items()
                       if isinstance(raw_map, dict) and str(v).strip() in outcomes}

        importance = str(entry.get("importance", "")).strip().title()
        out.append(Consolidation(
            decisions=members, id=kept, name=str(entry.get("name", "")).strip() or kept,
            outcomes=outcomes, outcome_map=outcome_map,
            capabilities=[str(c).strip().upper() for c in (entry.get("capabilities") or [])
                         if str(c).strip()],
            importance=importance if importance in ("Low", "Medium", "High") else "",
            rationale=prose(entry, "rationale")))
    return out


def review_structure(intake: IntakeData, complete: Optional[CompletionFn] = None,
                     context: str = "") -> StructureReview:
    """One call: proposals only, applied later and individually from the intake stage.

    Returns an empty review, logged rather than raised, where the call fails or the intake has
    nothing to look at -- a structure review is an optional extra pass, and a person looking for
    it to appear is a better failure than the intake stage itself breaking because of it.
    """
    if not intake.decisions and not intake.states:
        return StructureReview()

    complete = complete or ask_llm
    decision_ids = {d.id for d in intake.decisions}
    state_ids = {s.id for s in intake.states}
    bounded = {c.id for c in intake.capabilities if c.is_bounded}

    user = prompt_loader.render(
        _TASK_PROMPT, use_case=describe_use_case(intake), structure=describe_graph(intake),
        cds=prompt_loader.load("shared.cds"),
        hints=_render_hints(intake), context=supplementary_context(context))
    system = prompt_loader.load(_SYSTEM_PROMPT)

    try:
        reply = parse_json_object(call(
            complete, system, user, tier=config.stage_tier("STRUCTURE_REVIEW", config.JUDGEMENT)))
    except Exception as exc:
        logger.warning("Structure review call failed: %s", exc)
        return StructureReview()

    review = StructureReview(
        reconnections=_validate_reconnections(reply.get("reconnections"), decision_ids, state_ids),
        consolidations=_validate_consolidations(
            reply.get("consolidations"), decision_ids,
            # Only capabilities with a span count as blocks here -- see
            # _validate_consolidations. A capability nobody has bounded is a label.
            block_of={d.id: d.trigger_capability for d in intake.decisions
                      if d.trigger_capability in bounded}))
    logger.info("Structure review: %d reconnection(s), %d consolidation(s) proposed in 1 model "
                "call.", len(review.reconnections), len(review.consolidations))
    return review
