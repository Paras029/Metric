"""What the coverage stage shows on the page, rebuilt from the mappings rather than re-run.

Mapping conversations onto the scenario space is the expensive half of the stage -- one model call per
handful of transcripts -- and counting them is free. So the mappings are what the workspace keeps,
and everything here is derived from them each time the page is drawn.

That split is what makes the representation threshold usable. Moving the line between represented
and under-represented is a judgement about how much of the model owner's evidence is enough, and
a person
arrives at it by trying a number and looking at the result. If changing it re-ran the model,
nobody would try a second number.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..core.representation import CoverageReport, build_report
from ..llm.conversation_mapping import Mapping
from .workspace import Workspace


def stored_mappings(workspace: Workspace) -> List[Mapping]:
    """The last run's mappings, back in the form the report builder and the workbook take."""
    return [Mapping(conversation_id=entry.get("conversation_id", ""),
                    scenario_id=entry.get("scenario_id", ""),
                    confidence=entry.get("confidence", "low"),
                    intent=entry.get("intent", ""), ending=entry.get("ending", ""),
                    reason=entry.get("reason", ""),
                    declared_group=entry.get("declared_group", ""))
            for entry in workspace.coverage_mappings()]


def stored_report(workspace: Workspace, space) -> Optional[CoverageReport]:
    """The last run's coverage, counted at whatever threshold is set now."""
    mappings = stored_mappings(workspace)
    if not mappings:
        return None
    return build_report(mappings, space, threshold=workspace.coverage_threshold)


def coverage_view(workspace: Workspace, report: CoverageReport,
                  texts: Dict[str, str] = None) -> dict:
    """Everything the coverage panel renders, assembled here rather than in the markup.

    The counts and the confidences are shown side by side and never combined. Five conversations
    the matcher was sure about and five it was guessing at both count as five, because whether a
    weak match is evidence of coverage is a judgement about the matcher, and the person reading
    this page is in a far better position to make it than the matcher is.
    """
    texts = texts or {}
    by_id = {m.conversation_id: m for m in stored_mappings(workspace)}

    scenarios = []
    for entry in report.scenarios:
        scenario = entry.scenario
        scenarios.append({
            "id": scenario.id,
            "category": scenario.category,
            "materiality": scenario.materiality,
            "route": scenario.path_str or "probe",
            "description": (texts.get(scenario.id) or "")[:220],
            "count": entry.count,
            "confidence": entry.confidence_summary,
            "represented": entry.represented(report.threshold),
            "conversations": [{
                "id": conversation_id,
                "confidence": by_id[conversation_id].confidence if conversation_id in by_id
                              else "low",
                "reason": by_id[conversation_id].reason if conversation_id in by_id else "",
                "group": by_id[conversation_id].declared_group if conversation_id in by_id else "",
            } for conversation_id in entry.conversation_ids],
        })

    unmatched = [{"id": conversation_id,
                  "intent": by_id[conversation_id].intent if conversation_id in by_id else "",
                  "ending": by_id[conversation_id].ending if conversation_id in by_id else "",
                  "reason": by_id[conversation_id].reason if conversation_id in by_id else "",
                  "group": by_id[conversation_id].declared_group if conversation_id in by_id else ""}
                 for conversation_id in report.unmatched]

    groups = [{"group": group.group, "total": group.total, "agrees": group.agrees,
               "verdict": group.verdict, "dominant": group.dominant}
              for group in sorted(report.groups, key=lambda g: (g.agrees, g.group))]

    return {
        "summary": report.summary(),
        "threshold": report.threshold,
        "how_read": workspace.coverage.get("how_read", ""),
        "scenarios": scenarios,
        "unmatched": unmatched,
        "groups": groups,
        "gaps_only": workspace.pack_gaps_only,
        "under_represented": [s.scenario.id for s in report.under_represented()],
    }
