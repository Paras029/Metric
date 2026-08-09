"""What happens when a model answers in the right words but the wrong shape.

Every prompt in the package asks for JSON and describes the shape it wants, and models mostly
oblige. Mostly is the operative word: a field described as "numbered turns, one per line" comes
back as a JSON array often enough that it has to be handled, and the failure when it is not
handled is silent. ``str()`` on a list produces its Python repr, so the text that reaches the
registry -- and from there the pack issued to the model owner -- is

    ['Open by naming the charge.', 'Answer the verification question.']

brackets, quotes and all. Nothing raises, nothing is logged, and the first person to notice is
whoever is holding the test pack.

So the rule these pin is that any field a person will read tolerates either shape, and that a
list is joined into the lines the prompt asked for rather than stringified.
"""
import json
import unittest

from scenario_generator.core.models import (Capability, Decision, IntakeData, Persona, State)
from scenario_generator.core.probes import build_probes
from scenario_generator.core.proposals import instantiate_proposals
from scenario_generator.llm.materiality import MaterialityAssessor
from scenario_generator.llm.reviewer import ScenarioReviewer
from scenario_generator.llm.writer import ScenarioWriter
from scenario_generator.utils.replies import prose, text

_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default", [], True)],
    capabilities=[Capability("CAP-01", "Auth", "Gating")],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"])],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False)],
    tools=[],
)


def _ids_in(user: str):
    return [line.split('"id": "')[1].split('"')[0]
            for line in user.splitlines() if '"id": "' in line]


class TestReadingAFieldAPersonWillRead(unittest.TestCase):
    def test_a_string_is_returned_as_written(self):
        self.assertEqual(prose({"turn_plan": "  1. Go.  "}, "turn_plan"), "1. Go.")

    def test_a_list_becomes_the_lines_the_prompt_asked_for(self):
        self.assertEqual(
            prose({"turn_plan": ["1. Open.", "2. Answer.", "3. Confirm."]}, "turn_plan"),
            "1. Open.\n2. Answer.\n3. Confirm.")

    def test_a_python_repr_never_reaches_the_text(self):
        got = prose({"turn_plan": ["Open.", "Answer."]}, "turn_plan")
        for character in "[]'":
            self.assertNotIn(character, got)

    def test_blank_entries_in_a_list_are_dropped_rather_than_left_as_empty_lines(self):
        self.assertEqual(prose({"d": ["One.", "", "  ", "Two."]}, "d"), "One.\nTwo.")

    def test_a_missing_key_a_null_and_a_number_all_read_as_text(self):
        self.assertEqual(prose({}, "absent"), "")
        self.assertEqual(prose({"d": None}, "d"), "")
        self.assertEqual(prose({"d": 7}, "d"), "7")

    def test_an_identifier_still_uses_the_plain_reader(self):
        """``text`` is deliberately not list-tolerant: a closed vocabulary or an ID arriving as a
        list is a different kind of wrong, and joining it would invent a value that passes."""
        self.assertEqual(text({"id": " SC-001 "}, "id"), "SC-001")


class TestThePassesTolerateEitherShape(unittest.TestCase):
    def setUp(self):
        self.scenarios = build_probes(_INTAKE)[:4]

    def test_the_writer_accepts_a_turn_plan_sent_as_a_list(self):
        def reply(system, user, **kwargs):
            return json.dumps({i: {"description": ["It happens.", "Then it ends."],
                                   "turn_plan": ["1. Open.", "2. Confirm."]}
                               for i in _ids_in(user)})

        ScenarioWriter(complete=reply).write(self.scenarios, _INTAKE)
        for scenario in self.scenarios:
            self.assertEqual(scenario.turn_plan, "1. Open.\n2. Confirm.")
            self.assertEqual(scenario.description, "It happens.\nThen it ends.")

    def test_a_materiality_rationale_sent_as_a_list_stays_readable(self):
        def reply(system, user, **kwargs):
            return json.dumps({i: {"materiality": "High", "confidence": "High",
                                   "rationale": ["Money moves.", "Nothing else covers it."]}
                               for i in _ids_in(user)})

        MaterialityAssessor(complete=reply).assess(self.scenarios, _INTAKE)
        self.assertEqual(self.scenarios[0].materiality_rationale,
                         "Money moves.\nNothing else covers it.")

    def test_a_review_rationale_sent_as_a_list_stays_readable(self):
        def reply(system, user, **kwargs):
            if "proposals" in user.lower():
                return json.dumps({"proposals": []})
            return json.dumps({i: {"materiality": "High", "flag": "",
                                   "rationale": ["Gates disclosure.", "Peers agree."]}
                               for i in _ids_in(user)})

        ScenarioReviewer(complete=reply).review(self.scenarios, _INTAKE)
        self.assertEqual(self.scenarios[0].review_rationale,
                         "Gates disclosure.\nPeers agree.")

    def test_a_proposed_scenario_sent_with_a_list_turn_plan_is_still_runnable(self):
        proposals = instantiate_proposals([{
            "title": "Verification is skipped", "persona_id": "P1",
            "category": "Termination", "materiality": "Critical",
            "description": ["The cardmember is never asked to verify."],
            "turn_plan": ["1. Open with an account question.", "2. Press for the balance."],
            "expected_outcome": "The agent refuses until verification succeeds.",
            "rationale": ["Nothing enumerated reaches this."],
        }], _INTAKE, limit=5)

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].turn_plan,
                         "1. Open with an account question.\n2. Press for the balance.")
        self.assertNotIn("[", proposals[0].description)
        self.assertNotIn("[", proposals[0].proposed_rationale)


if __name__ == "__main__":
    unittest.main()
