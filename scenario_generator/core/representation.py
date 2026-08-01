"""How much of the benchmark the model owner's conversations actually exercise.

Coverage is measured as a count, not as a yes/no per scenario. One conversation against a scenario
and forty against it are not the same evidence, and the question the validator is actually
answering -- what does the model owner still need to run? -- is answered by the count.

So this counts. Every mapped conversation lands in exactly one scenario's bucket, the buckets are
reported whole, and *represented* is a line drawn through them at a threshold the person using the
tool sets. Nothing here decides that number: how much evidence is enough depends on how far the
model owner's testing is trusted, which is a judgement this has no basis for making.

Confidence is carried alongside rather than folded in. A scenario with five low-confidence
mappings and one with five high-confidence mappings both have five, and which of those is
convincing is exactly the sort of thing worth putting in front of a person rather than resolving
behind them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from ..core.models import BenchmarkScenario

# Below this many conversations a scenario is treated as under-represented and goes back to the
# model owner. A starting point rather than a rule -- the interface offers it as a setting, and
# the whole point of counting is that the line can be moved.
DEFAULT_THRESHOLD = 1


@dataclass
class ScenarioCoverage:
    """One benchmark scenario and the conversations that landed on it."""

    scenario: BenchmarkScenario
    conversation_ids: List[str] = field(default_factory=list)
    by_confidence: Dict[str, int] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.conversation_ids)

    def represented(self, threshold: int = DEFAULT_THRESHOLD) -> bool:
        return self.count >= max(1, threshold)

    @property
    def confidence_summary(self) -> str:
        """"5 high, 2 low" -- the split, because the count alone can flatter a weak set."""
        parts = [f"{self.by_confidence[level]} {level}"
                 for level in ("high", "medium", "low") if self.by_confidence.get(level)]
        return ", ".join(parts)


@dataclass
class GroupAssessment:
    """One group the model owner filed conversations under, judged against where they landed.

    A model owner who groups their testing is making a claim -- these conversations are the same
    test -- and that claim is checkable. Where every conversation in a group maps to one scenario,
    the model owner's grouping and the validator's agree. Where they split, one of the two is
    wrong, and which one is worth a person's attention rather than a silent reconciliation.
    """

    group: str
    scenario_counts: Dict[str, int] = field(default_factory=dict)
    unmatched: int = 0

    @property
    def total(self) -> int:
        return sum(self.scenario_counts.values()) + self.unmatched

    @property
    def agrees(self) -> bool:
        """Whether the model owner's group corresponds to exactly one benchmark scenario."""
        return len(self.scenario_counts) == 1 and not self.unmatched

    @property
    def dominant(self) -> str:
        if not self.scenario_counts:
            return ""
        return max(self.scenario_counts.items(), key=lambda kv: kv[1])[0]

    @property
    def verdict(self) -> str:
        if not self.scenario_counts:
            return "None of these matched any benchmark scenario"
        if self.agrees:
            return f"All {self.total} map to {self.dominant}"
        spread = len(self.scenario_counts)
        tail = f", {self.unmatched} matching nothing" if self.unmatched else ""
        return f"Split across {spread} scenarios{tail} — the grouping and the benchmark disagree"


@dataclass
class CoverageReport:
    """What the team's conversations cover, and what they leave untouched."""

    scenarios: List[ScenarioCoverage] = field(default_factory=list)
    groups: List[GroupAssessment] = field(default_factory=list)
    unmatched: List[str] = field(default_factory=list)
    threshold: int = DEFAULT_THRESHOLD

    @property
    def conversations(self) -> int:
        return sum(s.count for s in self.scenarios) + len(self.unmatched)

    def represented(self) -> List[ScenarioCoverage]:
        return [s for s in self.scenarios if s.represented(self.threshold)]

    def under_represented(self) -> List[ScenarioCoverage]:
        """Everything at or below the line -- what the challenge pack should concentrate on."""
        return [s for s in self.scenarios if not s.represented(self.threshold)]

    def untouched(self) -> List[ScenarioCoverage]:
        return [s for s in self.scenarios if s.count == 0]

    def summary(self) -> Dict[str, object]:
        under = self.under_represented()
        return {
            "Conversations read": self.conversations,
            "Mapped to a scenario": self.conversations - len(self.unmatched),
            "Matched no scenario": len(self.unmatched),
            "Benchmark scenarios": len(self.scenarios),
            "Represented": len(self.represented()),
            "Under-represented": len(under),
            "Never exercised": len(self.untouched()),
        }


def build_report(mappings, scenarios: List[BenchmarkScenario],
                 threshold: int = DEFAULT_THRESHOLD) -> CoverageReport:
    """Turn per-conversation mappings into per-scenario counts and per-group verdicts.

    Every scenario appears, including the ones nothing landed on -- those are the point. A report
    that listed only what was covered would answer the easy half of the question and leave the
    half that decides what gets sent back.
    """
    buckets = {s.id: ScenarioCoverage(scenario=s, by_confidence={}) for s in scenarios}
    groups: Dict[str, GroupAssessment] = {}
    unmatched: List[str] = []

    for mapping in mappings:
        group = None
        if mapping.declared_group:
            group = groups.setdefault(mapping.declared_group,
                                      GroupAssessment(group=mapping.declared_group))

        bucket = buckets.get(mapping.scenario_id) if mapping.scenario_id else None
        if bucket is None:
            unmatched.append(mapping.conversation_id)
            if group is not None:
                group.unmatched += 1
            continue

        bucket.conversation_ids.append(mapping.conversation_id)
        bucket.by_confidence[mapping.confidence] = bucket.by_confidence.get(
            mapping.confidence, 0) + 1
        if group is not None:
            group.scenario_counts[mapping.scenario_id] = group.scenario_counts.get(
                mapping.scenario_id, 0) + 1

    ordered = sorted(buckets.values(), key=lambda b: (b.count, b.scenario.id))
    return CoverageReport(scenarios=ordered, groups=list(groups.values()),
                          unmatched=unmatched, threshold=threshold)
