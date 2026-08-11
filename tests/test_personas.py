"""What counts as a persona.

A persona is a person arriving with an objective. Two objectives are always in play — someone who
wants the service to work, and someone who wants the agent to act outside its remit — and past
those, a persona has to earn its place by changing what the agent does.

The reason to be strict is arithmetic. Every persona multiplies the whole scenario space: enumerate
four temperaments and the same route is tested four times over while the routes that matter go
untested. "Impatient user" is not a different objective, it is the same objective in a different
tone, and the agent does not branch on tone.
"""
import unittest

from scenario_generator.ingest.drafting import MAX_PERSONAS, _personas


def _names(personas):
    return [p["name"] for p in personas]


class TestTheTwoThatAlwaysExist(unittest.TestCase):
    def test_a_draft_with_only_a_cooperative_user_still_gets_an_adversarial_one(self):
        personas = _personas({"personas": [
            {"id": "P1", "name": "Cardholder", "applies_to": "Filing a dispute",
             "is_default": True}]})
        self.assertEqual(len(personas), 2)
        self.assertEqual(personas[0]["name"], "Cardholder")
        self.assertTrue(any("Adversarial" in name for name in _names(personas)))

    def test_a_draft_with_no_personas_at_all_still_gets_both(self):
        personas = _personas({"personas": []})
        self.assertEqual(len(personas), 2)
        self.assertTrue(personas[0]["is_default"])

    def test_the_drafted_adversarial_persona_is_kept_rather_than_replaced(self):
        personas = _personas({"personas": [
            {"id": "P1", "name": "Cardholder", "applies_to": "Filing a dispute",
             "is_default": True},
            {"id": "P2", "name": "Fraudster", "applies_to": "Trying to move money out",
             "is_default": False}]})
        self.assertEqual(_names(personas), ["Cardholder", "Fraudster"])

    def test_exactly_one_persona_is_the_default(self):
        personas = _personas({"personas": [
            {"id": "P1", "name": "Cardholder", "applies_to": "Filing a dispute"},
            {"id": "P2", "name": "Merchant", "applies_to": "The agent asks a merchant for "
                                                           "evidence before deciding"}]})
        self.assertEqual(sum(1 for p in personas if p["is_default"]), 1)


class TestWhatDoesNotEarnItsPlace(unittest.TestCase):
    def test_a_persona_named_for_a_manner_is_dropped(self):
        personas = _personas({"personas": [
            {"id": "P1", "name": "Cardholder", "applies_to": "Filing a dispute",
             "is_default": True},
            {"id": "P2", "name": "Impatient user", "applies_to": "Wants a quick answer"},
            {"id": "P3", "name": "Confused user", "applies_to": "Does not know the terms"}]})
        self.assertNotIn("Impatient user", _names(personas))
        self.assertNotIn("Confused user", _names(personas))

    def test_a_persona_that_says_nothing_about_the_difference_is_dropped(self):
        personas = _personas({"personas": [
            {"id": "P1", "name": "Cardholder", "applies_to": "Filing a dispute",
             "is_default": True},
            {"id": "P2", "name": "Business customer", "applies_to": ""}]})
        self.assertNotIn("Business customer", _names(personas))

    def test_a_persona_the_agent_genuinely_treats_differently_is_kept(self):
        personas = _personas({"personas": [
            {"id": "P1", "name": "Cardholder", "applies_to": "Filing a dispute",
             "is_default": True},
            {"id": "P2", "name": "Merchant",
             "applies_to": "The agent asks a merchant for evidence before deciding"}]})
        self.assertIn("Merchant", _names(personas))

    def test_the_ceiling_holds_however_many_are_drafted(self):
        drafted = [{"id": "P1", "name": "Cardholder", "applies_to": "Filing a dispute",
                    "is_default": True}]
        drafted += [{"id": f"P{n}", "name": f"Segment {n}",
                     "applies_to": f"The agent routes segment {n} to a different queue"}
                    for n in range(2, 9)]
        self.assertLessEqual(len(_personas({"personas": drafted})), MAX_PERSONAS)


class TestTheScenarioSpaceStaysASensibleSize(unittest.TestCase):
    def test_two_personas_is_an_acceptable_answer(self):
        """The floor and the common case; nothing forces a third."""
        personas = _personas({"personas": [
            {"id": "P1", "name": "Cardholder", "applies_to": "Filing a dispute",
             "is_default": True},
            {"id": "P2", "name": "Adversarial user", "applies_to": "Trying to extract data"}]})
        self.assertEqual(len(personas), 2)


if __name__ == "__main__":
    unittest.main()
