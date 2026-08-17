"""Domain logic: intake parsing, the decision graph, scenario generation, probe selection and
proposal validation.

No file I/O beyond the intake workbook itself, and no model calls.
"""
from .context import load_context
from .generation import (bind_persona, build_turn_meta, categorise, instantiate_all,
                         instantiate_span, number_scenarios, peer_signals,
                         recommended_turns, required_variations, turn_plan_lines)
from .graph import DecisionGraph, Span, enumerate_by_span, enumerate_paths, spans_for
from .probes import applicable, build_probes, load_library
from .proposals import instantiate_proposal, instantiate_proposals
from .intake import read_intake, read_owner_scenarios, write_template
from .models import (CAPABILITY_TYPES, FUNCTIONAL_ORIGINS, ORIGINS, ScenarioRow,
                     Capability, Decision, IntakeData, OwnerScenario, Persona, Scenario,
                     State, Step, Tool, TurnMeta, canonical_origin)

__all__ = [
    "IntakeData", "Persona", "Capability", "Decision", "State", "Tool",
    "Scenario", "Step", "TurnMeta", "ScenarioRow", "OwnerScenario",
    "read_intake", "read_owner_scenarios", "write_template",
    "DecisionGraph", "Span", "enumerate_paths", "enumerate_by_span", "spans_for",
    "instantiate_all", "instantiate_span", "number_scenarios",
    "bind_persona", "build_turn_meta", "categorise",
    "fallback_name", "recommended_turns", "required_variations", "turn_plan_lines",
    "build_probes", "load_library", "applicable", "load_context", "peer_signals",
    "instantiate_proposal", "instantiate_proposals",
    "CAPABILITY_TYPES", "FUNCTIONAL_ORIGINS", "ORIGINS", "canonical_origin",
]
