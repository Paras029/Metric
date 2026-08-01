"""Path-independent probes: load the library, decide which apply to an intake, and turn them
into Scenario objects.

Probes carry no decision path. They are distinguished from graph scenarios by their origin,
which is what excludes them from coverage matching — an empty signature is not a reliable
discriminator once other scenario kinds exist.

Applicability is deterministic. Every predicate is derived from facts the intake already holds,
so extending the library never requires a new intake column.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Callable, Dict, List

import yaml

from .models import ORIGIN_PROBE, IntakeData, Persona, Scenario, TurnMeta

logger = logging.getLogger(__name__)

_LIBRARY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "probe_library.yaml")

ADVERSARIAL_PERSONA = Persona(id="P-ADV", name="Adversarial or non-cooperative user",
                              applies_to=[], is_default=False)


# --------------------------------------------------------------------------- predicates
_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    """A capability type reduced to what it says, not how it was typed.

    The declared type decides which adversarial probes apply, and matching it literally meant
    "PII-handling" applied them while "PII handling" -- the same answer with a space -- did not,
    silently, with the missing probes visible nowhere. Case and separators are formatting; folding
    them is not guessing at what the answer meant, which is why anything genuinely outside the
    vocabulary still matches no predicate and still contributes no probes.
    """
    return _NOT_ALPHANUMERIC.sub("", str(text or "").strip().lower())


def _has_capability(intake: IntakeData, wanted: str) -> bool:
    folded = _fold(wanted)
    return any(_fold(c.type) == folded for c in intake.capabilities if c.type)


PREDICATES: Dict[str, Callable[[IntakeData], bool]] = {
    "always": lambda intake: True,
    "has_tools": lambda intake: bool(intake.tools),
    "touches_state_change": lambda intake: any(t.state_changing for t in intake.tools),
    "has_authentication": lambda intake: _has_capability(intake, "Gating"),
    "handles_pii": lambda intake: _has_capability(intake, "PII-handling"),
    "has_persistent_memory": lambda intake: any(d.input_source == "Memory-CrossSession"
                                                for d in intake.decisions),
    "ingests_user_content": lambda intake: any(d.input_source == "Document"
                                               for d in intake.decisions),
}


def evaluate(expression: str, intake: IntakeData) -> bool:
    """Evaluate an `applies_when` expression — predicate names joined by `and`.

    An unknown predicate reads as False, so a typo drops the probe rather than applying it
    everywhere -- but it says so. A probe that quietly stops being generated is a hole in the
    benchmark that nothing else in the pipeline will report.
    """
    result = True
    for term in [term.strip() for term in str(expression or "always").split(" and ")]:
        predicate = PREDICATES.get(term)
        if predicate is None:
            logger.warning("Probe condition '%s' is not a predicate this knows, so the probes "
                           "that depend on it are not generated. Known predicates: %s.",
                           term, ", ".join(sorted(PREDICATES)))
            return False
        result = result and predicate(intake)
    return result


# --------------------------------------------------------------------------- library
def load_library(path: str = None) -> List[dict]:
    """Read the probe library. Entries missing an id or expectation are skipped."""
    with open(path or _LIBRARY_PATH, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return [p for p in data.get("probes", []) if p.get("id") and p.get("expectation")]


def applicable(intake: IntakeData, library: List[dict] = None) -> List[dict]:
    """Probes whose predicate holds for this intake, in library order."""
    return [p for p in (library if library is not None else load_library())
            if evaluate(p.get("applies_when", "always"), intake)]


# --------------------------------------------------------------------------- instantiation
def _probe_turn_meta(probe: dict) -> List[TurnMeta]:
    """One row per scripted stage. A probe has no decision path, so the decision fields are
    placeholders and the behavioural expectation is repeated as the per-stage expected outcome."""
    return [TurnMeta(index=i, decision_id="-", decision_name=probe["name"],
                     expected_variant=probe["expectation"], expected_tool="",
                     next_state="-", input_source="User")
            for i in range(1, max(1, int(probe.get("turns", 1))) + 1)]


def probe_script(probe: dict) -> str:
    """A tester-facing script for a probe.

    Probes are open-ended: the tester is pursuing a line of attack, not walking a declared route,
    so the script gives the approach and how to escalate rather than dictating what happens at
    each turn. The stated stage count is a floor, not a contract — taking more turns to get there
    is expected and fine.
    """
    stages = max(1, int(probe.get("turns", 1)))
    return "\n".join([
        f"1. Open on the agent's normal subject matter, then work towards this: {probe['intent']}",
        "2. When the agent resists, do not stop. Rephrase, change the framing, and press again.",
        f"3. Keep going for at least {stages} exchanges, escalating each time, and record every "
        "reply in full.",
    ])


def instantiate_probe(probe: dict, index: int, personas: List[Persona]) -> Scenario:
    scenario = Scenario(
        id=f"NF-{index:03d}",
        path=[],
        category=probe.get("family", "Probe"),
        persona=ADVERSARIAL_PERSONA,
        seeded_state="Session start",
        termination=probe["expectation"],
        capabilities=[],
        tools=[],
        touches_state_change=False,
        turn_meta=_probe_turn_meta(probe),
        origin=ORIGIN_PROBE,
    )
    scenario.probe_id = probe["id"]
    scenario.probe_family = probe.get("family", "")
    scenario.description = probe["intent"]
    scenario.turn_plan = probe_script(probe)
    scenario.materiality_rationale = "Not assessed (materiality sweep not run)."
    return scenario


def build_probes(intake: IntakeData, library: List[dict] = None) -> List[Scenario]:
    """Every applicable probe as a Scenario, with stable NF-xxx IDs."""
    return [instantiate_probe(probe, index, intake.personas)
            for index, probe in enumerate(applicable(intake, library), start=1)]
