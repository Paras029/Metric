"""Guards on the prompt library.

Prompt wording is editable by people who are not changing code, which is the point of keeping it
in its own files. These tests are what stops an edit on one side of that boundary from quietly
breaking the other: a renamed placeholder, a prompt a pass expects but nobody wrote, or wording
that reintroduces the one thing the data template must never contain.
"""
import json
import unittest

from scenario_generator.core.models import (Decision, IntakeData, Persona, State, Tool)
from scenario_generator.core.probes import build_probes
from scenario_generator.llm import prompt_loader
from scenario_generator.llm.prompt_loader import PromptError
from scenario_generator.llm.writer import ScenarioWriter

# Every prompt a pass loads by name, with the placeholders that pass supplies. Kept here rather
# than discovered, so that deleting a prompt or renaming a placeholder fails loudly.
_CONTRACT = {
    "shared.mission": set(),
    "shared.materiality_scale": set(),
    "shared.house_style": set(),
    "writer.system": set(),
    "writer.graph_scenario": {"use_case", "context", "house_style", "scenarios"},
    "writer.probe": {"use_case", "context", "house_style", "scenarios"},
    "writer.repair": {"use_case", "context", "house_style", "current", "problems", "scenario"},
    "materiality.system": set(),
    "materiality.task": {"mission", "scale", "use_case", "context", "scenarios"},
    "reviewer.system": set(),
    "reviewer.approach": set(),
    "reviewer.field_guide": set(),
    "reviewer.assess": {"owner", "total", "digest", "materiality", "batch"},
    "reviewer.category": {"owner", "total", "digest", "categories", "batch"},
    "reviewer.propose": {"owner", "total", "digest", "limit", "categories", "materiality"},
    "reviewer.adjudicate": {"total", "digest", "materiality", "scale_values", "batch"},
    "reviewer.owner_block": {"owner_scenarios"},
    "ingest.system": set(),
    "ingest.read": {"questions", "corpus", "documents"},
    "ingest.resolve": {"established", "questions", "corpus"},
    "ingest.diagram_read": {"filename", "position"},
    "ingest.diagram_synthesize": {"facets", "document", "readings"},
    "ingest.diagram_reconcile": {"facets", "document", "readings"},
    "ingest.diagram_only": {"facets", "filename"},
    "ingest.diagram_repair": {"document", "structure", "problems"},
    "intake.draft": {"context", "structure"},
    "intake.revise": {"context", "current", "structure"},
    "intake.repair": {"context", "current", "structure", "problems", "enumeration"},
    "coverage.system": set(),
    "coverage.map": {"use_case", "scenarios", "conversations"},
    "coverage.migrate": {"origin", "sample"},
    "structure_review.system": set(),
    "structure_review.task": {"use_case", "structure", "hints", "context"},
}

_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default user", [], True)],
    capabilities=[],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass"])],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False)],
    tools=[Tool("Filing API", "CAP-01", True)],
)


class TestPromptLibrary(unittest.TestCase):
    def test_every_prompt_the_code_uses_exists_and_is_not_empty(self):
        for name in _CONTRACT:
            self.assertTrue(prompt_loader.load(name).strip(), f"{name} is empty")

    def test_placeholders_match_what_the_passes_supply(self):
        for name, expected in _CONTRACT.items():
            self.assertEqual(prompt_loader.placeholders(name), expected,
                             f"{name} does not expect the placeholders its caller passes")

    def test_no_prompt_file_is_orphaned(self):
        """A file nobody loads is either dead weight or a caller that was never wired up."""
        self.assertEqual(set(prompt_loader.available()), set(_CONTRACT))

    def test_every_prompt_renders(self):
        for name, slots in _CONTRACT.items():
            rendered = prompt_loader.render(name, **{slot: "x" for slot in slots})
            self.assertNotIn("{{", rendered, f"{name} still has an unfilled placeholder")

    def test_a_missing_value_is_an_error_rather_than_a_gap(self):
        with self.assertRaises(PromptError):
            prompt_loader.render("reviewer.owner_block")

    def test_a_value_with_no_slot_is_an_error_rather_than_ignored(self):
        with self.assertRaises(PromptError):
            prompt_loader.render("reviewer.owner_block", owner_scenarios="x", renamed="y")

    def test_json_examples_survive_rendering(self):
        """Prompts embed JSON, so braces must pass through untouched by the placeholder pass."""
        self.assertIn('{"proposals": []}', prompt_loader.render(
            "reviewer.propose", owner="", total=1, digest="d", limit=1,
            categories="c", materiality="m"))


class TestWriterWithholdsTheAnswerKey(unittest.TestCase):
    """The writer's output is issued to the team that owns the agent, so the expected outcome is
    withheld from the call itself rather than only forbidden by the prompt."""

    def test_payload_carries_no_terminal_state(self):
        writer = ScenarioWriter(complete=lambda *a, **k: "{}")
        for scenario in build_probes(_INTAKE):
            scenario.termination = "SENTINEL-EXPECTED-OUTCOME"
            payload = json.dumps(writer._payload(scenario))
            self.assertNotIn("SENTINEL-EXPECTED-OUTCOME", payload)

    def test_prompts_carry_the_non_disclosure_rule(self):
        house_style = prompt_loader.load("shared.house_style").lower()
        self.assertIn("never state", house_style)
        for name in ("writer.graph_scenario", "writer.probe"):
            self.assertIn("{{house_style}}", prompt_loader.load(name))


if __name__ == "__main__":
    unittest.main()
