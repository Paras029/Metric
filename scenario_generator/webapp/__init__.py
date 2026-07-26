"""Local web interface for the scenario generator.

Presentation only. Every stage delegates to the same pipeline functions the command line uses,
so the two front ends cannot drift, and a workspace can move between them.

    python -m scenario_generator.webapp
"""
from .app import create_app, main
from .workspace import Workspace

__all__ = ["create_app", "main", "Workspace"]
