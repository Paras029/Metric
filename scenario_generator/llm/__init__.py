"""LLM layer: the gateway client, the prompt library, and the passes run over scenarios.

    ScenarioWriter       owner-facing description and turn plan.
    MaterialityAssessor  a separate sweep assigning materiality with cross-scenario context.
    MetadataExtractor    maps a modeling team's free-text scenarios onto the intake vocabulary.
    ScenarioReviewer     final whole-registry sweep: revises materiality, proposes additions.

Prompt wording lives in ``scenario_generator/prompts`` and is reached through
:mod:`~scenario_generator.llm.prompt_loader`. What each pass builds from intake data -- the use
case description, the declared graph, the benchmark digest -- lives in
:mod:`~scenario_generator.llm.context`.
"""
from .context import describe_graph, describe_use_case
from .extractor import MetadataExtractor
from .gateway import ask_llm
from .materiality import MaterialityAssessor, NullMaterialityAssessor
from .reviewer import NullReviewer, ScenarioReviewer
from .writer import NullWriter, ScenarioWriter

__all__ = ["ask_llm", "describe_use_case", "describe_graph", "ScenarioWriter", "NullWriter",
          "MaterialityAssessor", "NullMaterialityAssessor", "MetadataExtractor",
          "ScenarioReviewer", "NullReviewer"]
