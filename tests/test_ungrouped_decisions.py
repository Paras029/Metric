"""A decision belonging to no capability is still a branch of a live system.

Capabilities are drawn by hand, and between two of them there is usually something nobody filed
under a heading: a consent gate, a channel check, a step that belongs to the flow rather than to
any one block. Before this, those decisions were walked by nothing at all — every outcome went
untested and the declaration looked complete while it happened, which is the quietest way a
scenario space can have a hole in it.

They are walked under a span of their own, from wherever the covered part of the graph hands into
them to wherever the flow rejoins a capability or stops — the same rule a drawn span follows, so a
scenario out of a gap reads like any other.
"""
import unittest

from scenario_generator.core.graph import (UNASSIGNED, DecisionGraph, enumerate_by_span,
                                           spans_for)
from scenario_generator.core.generation import instantiate_span, number_scenarios
from scenario_generator.core.models import Capability, Decision, Persona, State

# Identification, then a consent gate nobody filed, then verification.
_DECISIONS = [
    Decision("DEC-01", "Identify the cardmember", "CAP-01", "", ["Identified", "Cannot identify"]),
    Decision("DEC-05", "Take consent to proceed", "", "", ["Given", "Refused"]),
    Decision("DEC-02", "Verify the cardmember", "CAP-02", "", ["Verified", "Refused"]),
]
_STATES = [
    State("S-00", "Start", "The chat opens", ["DEC-01"], False),
    State("S-01", "DEC-01=Identified", "Identified", ["DEC-05"], False),
    State("S-09", "DEC-01=Cannot identify", "Locked out", [], True, "Termination"),
    State("S-02", "DEC-05=Given", "Consent given", ["DEC-02"], False),
    State("S-08", "DEC-05=Refused", "Consent refused", [], True, "Termination"),
    State("S-03", "DEC-02=Verified", "Verified", [], True, "Happy path"),
    State("S-04", "DEC-02=Refused", "Refused", [], True, "Escalation"),
]
_CAPABILITIES = [
    Capability("CAP-01", "Identification", "Gating", ("S-00",), ("S-01", "S-09")),
    Capability("CAP-02", "Verification", "Gating", ("S-02",), ("S-03", "S-04")),
]
_PERSONAS = [Persona("P1", "Cardmember", ["Wants in"], True)]


def _graph():
    return DecisionGraph(_DECISIONS, _STATES)


def _walked():
    taken = set()
    for _, walked, augmented in enumerate_by_span(_graph(), _CAPABILITIES):
        for path in walked + augmented:
            taken.update((step.decision_id, step.variant) for step in path)
    return taken


class TestNothingDeclaredGoesUnwalked(unittest.TestCase):
    def test_every_declared_outcome_is_exercised_somewhere(self):
        declared = {(d.id, v) for d in _DECISIONS for v in d.variants}
        self.assertEqual(declared - _walked(), set())

    def test_the_ungrouped_decision_gets_a_span_of_its_own(self):
        spans = [s for s in spans_for(_graph(), _CAPABILITIES)
                 if s.capability_id == UNASSIGNED]
        self.assertEqual([s.entry_state for s in spans], ["S-01"],
                         "the gap is entered where identification hands on")

    def test_it_ends_where_a_capability_starts_or_the_flow_stops(self):
        span = next(s for s in spans_for(_graph(), _CAPABILITIES)
                    if s.capability_id == UNASSIGNED)
        self.assertIn("S-02", span.exit_states, "does not stop where verification begins")
        self.assertIn("S-08", span.exit_states, "does not stop at its own ending")

    def test_its_scenarios_say_where_they_start(self):
        graph = _graph()
        out = []
        for span, walked, augmented in enumerate_by_span(graph, _CAPABILITIES):
            out += instantiate_span(span, walked, augmented, graph, _PERSONAS, [])
        loose = [s for s in number_scenarios(out) if s.capability_id == UNASSIGNED]
        self.assertTrue(loose)
        for scenario in loose:
            self.assertIn("Identified", scenario.precondition)

    def test_a_graph_where_everything_is_grouped_gains_no_extra_span(self):
        """The gap span exists for a gap. A tidy declaration should not grow a phantom block."""
        tidy = [Capability("CAP-01", "Identification", "Gating", ("S-00",), ("S-01", "S-09")),
                Capability("CAP-03", "Consent", "Gating", ("S-01",), ("S-02", "S-08")),
                Capability("CAP-02", "Verification", "Gating", ("S-02",), ("S-03", "S-04"))]
        self.assertNotIn(UNASSIGNED,
                         {s.capability_id for s in spans_for(_graph(), tidy)})

    def test_an_undivided_graph_still_walks_whole(self):
        self.assertEqual([s.is_whole_graph for s in spans_for(_graph(), [])], [True])


class TestCapabilityLevelDocumentationIsCaught(unittest.TestCase):
    """Model owners often describe an agent at capability level and nothing finer. Reading "the
    agent identifies the customer" as a decision invents its outcomes, and scenarios built on
    invented outcomes cannot be told from real ones by anybody downstream."""

    def _intake(self, decisions, capabilities):
        from scenario_generator.core.models import IntakeData
        return IntakeData(
            use_case={"Use case name": "X", "Business objective": "Y", "Agent type": "Chatbot",
                      "Channel / modality": "App", "Human handoff triggers": "n",
                      "Safety requirements": "n", "Success criteria": "done"},
            personas=_PERSONAS, capabilities=capabilities, decisions=decisions,
            states=[State("S-00", "Start", "Opens", [decisions[0].id], False),
                    State("S-01", f"{decisions[0].id}={decisions[0].variants[0]}", "One",
                          [], True, "Happy path"),
                    State("S-02", f"{decisions[0].id}={decisions[0].variants[1]}", "Two",
                          [], True, "Termination")],
            tools=[])

    def _granularity_questions(self, intake):
        from scenario_generator.core.gaps import find_gaps
        return [g for g in find_gaps(intake) if g.field == "granularity"]

    def test_a_capability_holding_one_decision_of_the_same_name_is_questioned(self):
        intake = self._intake(
            [Decision("DEC-01", "Identify the customer", "CAP-01", "", ["Success", "Failure"])],
            [Capability("CAP-01", "Identify the customer", "Gating")])
        asked = self._granularity_questions(intake)
        self.assertEqual(len(asked), 1)
        self.assertIn("actually decide", asked[0].question)

    def test_wording_that_differs_only_in_padding_is_still_caught(self):
        intake = self._intake(
            [Decision("DEC-01", "Customer identification check", "CAP-01", "",
                      ["Success", "Failure"])],
            [Capability("CAP-01", "Identify the customer", "Gating")])
        self.assertEqual(len(self._granularity_questions(intake)), 1)

    def test_a_capability_with_a_genuinely_different_single_decision_is_left_alone(self):
        intake = self._intake(
            [Decision("DEC-01", "Match the last four digits", "CAP-01", "",
                      ["Matches", "Does not match"])],
            [Capability("CAP-01", "Identification", "Gating")])
        self.assertEqual(self._granularity_questions(intake), [])

    def test_a_capability_with_several_decisions_is_left_alone(self):
        intake = self._intake(
            [Decision("DEC-01", "Identification", "CAP-01", "", ["A", "B"]),
             Decision("DEC-02", "Second step", "CAP-01", "", ["C", "D"])],
            [Capability("CAP-01", "Identification", "Gating")])
        self.assertEqual(self._granularity_questions(intake), [])


if __name__ == "__main__":
    unittest.main()


class TestTheDefinitionReachesEveryPromptThatWritesAGraph(unittest.TestCase):
    """Three prompts describing the same three things in slightly different words is how two of
    them end up describing something else. There is one definition, and it goes everywhere."""

    MARK = "A capability is a group of decisions"

    def test_it_is_in_every_prompt_that_builds_or_restructures_a_declaration(self):
        from scenario_generator.ingest.drafting import _cds, _wiring
        from scenario_generator.ingest.extraction import _vocabulary
        from scenario_generator.llm import prompt_loader

        rendered = {
            "intake.draft": prompt_loader.render("intake.draft", context="c", cds=_cds(),
                                                 wiring=_wiring(), structure="s"),
            "intake.repair": prompt_loader.render("intake.repair", context="c", current="x",
                                                  cds=_cds(), wiring=_wiring(), structure="s",
                                                  enumeration="e", problems="p"),
            "intake.revise": prompt_loader.render("intake.revise", context="c", current="x",
                                                  cds=_cds(), wiring=_wiring(), structure="s"),
            "structure_review.task": prompt_loader.render(
                "structure_review.task", use_case="u", structure="s", cds=_cds(), hints="h",
                context="c"),
            "reviewer.propose": prompt_loader.render(
                "reviewer.propose", owner="", total=1, digest="d", limit=1, categories="c",
                materiality="m", cds=_cds(), blocks="b", hard="h"),
            "the diagram prompts": _vocabulary(),
        }
        for name, text in rendered.items():
            with self.subTest(prompt=name):
                self.assertIn(self.MARK, text)

    def test_it_says_what_to_do_with_capability_level_documentation(self):
        """The instruction that stops invented decisions, which nothing downstream can detect."""
        from scenario_generator.llm import prompt_loader
        definition = prompt_loader.load("shared.cds")
        self.assertIn("Do **not** manufacture atomic decisions", definition)
