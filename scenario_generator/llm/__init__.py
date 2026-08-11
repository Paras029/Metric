"""LLM layer: the gateway client, the prompt library, and the passes run over scenarios.

    ScenarioWriter       owner-facing description and turn plan.
    MaterialityAssessor  a separate sweep assigning materiality with cross-scenario context.
    ScenarioReviewer     final whole-space sweep: revises materiality, proposes additions.
    review_structure     one intake-stage call proposing reconnections and consolidations.

Prompt wording lives in ``scenario_generator/prompts`` and is reached through
:mod:`~scenario_generator.llm.prompt_loader`. What each pass builds from intake data -- the use
case description, the declared graph, the scenario space digest -- lives in
:mod:`~scenario_generator.llm.context`.
"""
from .context import describe_graph, describe_use_case
from .gateway import ask_llm
from .materiality import MaterialityAssessor, NullMaterialityAssessor
from .reviewer import NullReviewer, ScenarioReviewer
from .structure_review import Consolidation, Reconnection, StructureReview, review_structure
from .writer import NullWriter, ScenarioWriter

__all__ = ["ask_llm", "describe_use_case", "describe_graph", "ScenarioWriter", "NullWriter",
          "MaterialityAssessor", "NullMaterialityAssessor",
          "ScenarioReviewer", "NullReviewer", "review_structure", "StructureReview",
          "Reconnection", "Consolidation"]
