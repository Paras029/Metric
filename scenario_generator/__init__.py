"""Agentic scenario generator: intake -> benchmark scenarios -> coverage of existing testing.

Public surface for programmatic use:

    from scenario_generator import build_graph, build_probes_stage, refine, \
        assess_materiality, review, map_conversation_coverage, generate
    from scenario_generator.core import IntakeData, Scenario
    from scenario_generator.llm import ScenarioWriter, MaterialityAssessor, ScenarioReviewer

The submodules `core`, `llm`, `io` and `utils` are also importable directly.
"""
from .llm import (MaterialityAssessor, NullMaterialityAssessor, NullReviewer, NullWriter,
                  ScenarioReviewer, ScenarioWriter)
from .pipeline import (assess_materiality, build_graph, build_pack, build_probes_stage,
                       build_scenarios, generate, map_conversation_coverage, refine, review)

__version__ = "5.0.0"

__all__ = [
    "build_graph", "build_probes_stage", "refine", "assess_materiality", "review",
    "build_pack",
    "generate", "map_conversation_coverage", "build_scenarios",
    "ScenarioWriter", "NullWriter",
    "MaterialityAssessor", "NullMaterialityAssessor",
    "ScenarioReviewer", "NullReviewer",
]
