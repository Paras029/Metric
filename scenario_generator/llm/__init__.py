"""LLM layer: the gateway client, shared prompt context, and the passes run over scenarios.

    ScenarioWriter       owner-facing description and turn plan.
    MaterialityAssessor  a separate sweep assigning materiality with cross-scenario context.
    MetadataExtractor    maps a modeling team's free-text scenarios onto the intake vocabulary.
    ScenarioReviewer     final whole-registry sweep: revises materiality, proposes additions.
"""
from .extractor import MetadataExtractor
from .gateway import ask_llm
from .prompts import describe_graph, describe_use_case
from .materiality import MaterialityAssessor, NullMaterialityAssessor
from .reviewer import NullReviewer, ScenarioReviewer
from .writer import NullWriter, ScenarioWriter

__all__ = ["ask_llm", "describe_use_case", "describe_graph", "ScenarioWriter", "NullWriter",
          "MaterialityAssessor", "NullMaterialityAssessor", "MetadataExtractor",
          "ScenarioReviewer", "NullReviewer"]
