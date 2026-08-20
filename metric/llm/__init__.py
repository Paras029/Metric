"""Model plumbing: how a call is made, tiered, batched, metered and cancelled.

Every pass that *makes* calls lives with the step that needs it, under ``metric.phases``.
Nothing here knows what a scenario is.
"""
from metric.llm.describe import describe_enumeration, describe_graph, describe_use_case
from metric.llm.gateway import ask_llm

__all__ = ["ask_llm", "describe_use_case", "describe_graph", "describe_enumeration"]
