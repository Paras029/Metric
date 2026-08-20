"""The declared graph as one self-contained page, built from the workbooks.

A graph gets quoted in a validation report and mailed to a model owner, and both of those are done
today with a screenshot -- which loses the zoom at exactly the size that needs one. This produces
the same drawing with the same reading controls, in a file that works with nothing running: the
stylesheet and the script are inlined rather than fetched, so it survives being emailed, attached,
or opened from a network share months later.

**Built from the workbooks, not from a workspace.** Point it at an intake and it draws that intake;
point it at a scenario space metadata workbook as well and the page gains the scenario list, where
opening a scenario lights the route it walks. Correct a row in the intake, build it again, and the
page is corrected -- which is what makes it something to keep rather than a picture that was true
once. Nothing about it depends on the interface, so the same page comes out of the command line and
out of a running workspace, and the two cannot drift.

Rendered with Jinja against the same template and the same ``graph.js`` the interface serves, for
the same reason: two copies of the drawing code is how the downloadable graph quietly stops
matching the graph on screen.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from jinja2 import Environment, FileSystemLoader, select_autoescape

from metric.domain.models import IntakeData, Scenario
from metric.web.graph.view import graph_summary, render_blocks_svg, render_svg, routes
from metric.web.scenariolist import to_row

WEB = Path(__file__).resolve().parent.parent
TEMPLATE = "graph_standalone.html"


def _environment() -> Environment:
    return Environment(loader=FileSystemLoader(str(WEB / "templates")),
                       autoescape=select_autoescape(["html"]))


def render_graph_page(intake: IntakeData, scenarios: Optional[Sequence[Scenario]] = None,
                      title: str = "") -> str:
    """The whole page as a string: the drawing, its controls, and the scenarios that walk it."""
    walked = routes(intake, scenarios or [])
    named = {c.id: c.name or c.id for c in intake.capabilities}

    # Only the scenarios whose route the drawing can place. One that cannot be placed would sit in
    # the list looking selectable and light nothing when opened, which reads as the page being
    # broken rather than as the scenario having no drawable route.
    rows: List[dict] = []
    for scenario in (scenarios or []):
        if scenario.id in walked:
            rows.append({**to_row(scenario, named), "route": walked[scenario.id]})

    return _environment().get_template(TEMPLATE).render(
        title=title or intake.name,
        graph_svg=render_svg(intake),
        blocks_svg=render_blocks_svg(intake),
        graph_facts=graph_summary(intake),
        rows=rows,
        drawn=datetime.now().strftime("%d %b %Y"),
        stylesheet=(WEB / "static" / "app.css").read_text(encoding="utf-8"),
        script=(WEB / "static" / "graph.js").read_text(encoding="utf-8"))


def write_graph_page(intake_path: str, output_path: str,
                     scenarios_path: Optional[str] = None) -> str:
    """Build the page from an intake workbook, optionally with its scenario space beside it.

    Takes paths rather than objects so that the command line, the interface and a person with two
    spreadsheets all reach it the same way. Returns the path written.
    """
    from metric.domain.intake import read_intake
    from metric.domain import read_scenarios

    intake = read_intake(intake_path)
    scenarios = read_scenarios(scenarios_path, intake) if scenarios_path else []
    Path(output_path).write_text(render_graph_page(intake, scenarios), encoding="utf-8")
    return output_path
