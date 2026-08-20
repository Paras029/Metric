"""METRIC - build an independent test benchmark for a conversational agent, and measure what
the agent's owner already tests.
"""
from metric.phases.scenario_generator.materiality.assess import (MaterialityAssessor,
                                                                 NullMaterialityAssessor)
from metric.phases.scenario_generator.review.reviewer import NullReviewer, ScenarioReviewer
from metric.phases.scenario_generator.scenarios.writer import NullWriter, ScenarioWriter
from metric.pipeline import (assess_materiality, build_graph, build_pack, build_probes_stage,
                             build_scenario_space, generate, map_conversation_coverage, refine,
                             review)

__version__ = "5.0.0"

__all__ = [
    "build_graph", "build_probes_stage", "refine", "assess_materiality", "review", "build_pack",
    "generate", "map_conversation_coverage", "build_scenario_space",
    "ScenarioWriter", "NullWriter",
    "MaterialityAssessor", "NullMaterialityAssessor",
    "ScenarioReviewer", "NullReviewer",
]
