"""Domain logic: intake parsing, the decision graph, scenario generation, probe selection and
proposal validation.

No file I/O beyond the intake workbook itself, and no model calls.
"""
from .context import load_context
from .generation import (bind_persona, build_turn_meta, categorise, instantiate_all, peer_signals,
                         recommended_turns, required_runs, turn_plan_lines)
from .graph import DecisionGraph, enumerate_paths
from .probes import applicable, build_probes, load_library
from .proposals import instantiate_proposal, instantiate_proposals
from .intake import read_intake, read_owner_scenarios, write_template
from .models import (CAPABILITY_TYPES, FUNCTIONAL_ORIGINS, BenchmarkScenario, Capability,
                     Decision, IntakeData, OwnerScenario, Persona, Scenario, State, Step, Tool,
                     TurnMeta)

__all__ = [
    "IntakeData", "Persona", "Capability", "Decision", "State", "Tool",
    "Scenario", "Step", "TurnMeta", "BenchmarkScenario", "OwnerScenario",
    "read_intake", "read_owner_scenarios", "write_template",
    "DecisionGraph", "enumerate_paths",
    "instantiate_all", "bind_persona", "build_turn_meta", "categorise",
    "recommended_turns", "required_runs", "turn_plan_lines",
    "build_probes", "load_library", "applicable", "load_context", "peer_signals",
    "instantiate_proposal", "instantiate_proposals",
    "CAPABILITY_TYPES", "FUNCTIONAL_ORIGINS",
]
