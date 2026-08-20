"""Checking a generative pass with code, and re-asking only about what failed.

Three passes in this package already work this way — the diagram reading, the intake draft, and
now the scenario writer — and the shape is the same each time. Produce something. Audit it
deterministically. Put the *specific* failures back to the model, named, once. It is the answer to
"can we loop until the extraction is good" that does not cost a loop: the audit is free, the
repair is one call over only what is broken, and a second attempt that is not strictly cleaner
than the first is thrown away.

The check that matters most is the answer-key one. The writer is never shown where a route ends,
but it is shown the outcome of every step on the way, so a route whose last step is "Identity
check = Fail" can be written up as "and the account is locked" without the model having been told
anything. That text is issued to the model owner. If it states the ending, the exercise measures
nothing, and it fails silently — the pack looks complete either way.

The other half of this file is the adjudication: a third opinion, but only where the two readings
the scenario space already pays for landed two or more tiers apart.
"""
import json
import unittest

from metric.domain.models import (Capability, Decision, IntakeData, Persona, State,
                                            Tool)
from metric.phases.scenario_generator.scenarios import quality
from metric.phases.scenario_generator.review.reviewer import ADJUDICATE_GAP, ScenarioReviewer, _tiers_apart
from metric.phases.scenario_generator.scenarios.writer import ScenarioWriter
from metric.pipeline import build_scenario_space

_INTAKE = IntakeData(
    use_case={"Use case name": "Disputes", "Business objective": "Resolve disputes"},
    personas=[Persona("P1", "Cardholder", [], True)],
    capabilities=[Capability("CAP-01", "Auth", "Gating")],
    decisions=[Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass", "Fail"])],
    states=[State("S-00", "Start", "Session begins", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "The dispute is filed and a reference number is given",
                  [], True, "Happy path"),
            State("S-02", "DEC-01=Fail", "Locked out after two failed attempts",
                  [], True, "Termination")],
    tools=[Tool("Identity service", "CAP-01", True)],
)

_GOOD = {"name": "Identity fails twice",
         "description": "The cardmember gets the security question wrong twice in a row while "
                        "raising a dispute about a charge they do not recognise.",
         "turn_plan": "1. Open by naming the charge.\n2. Answer the security question wrongly."}


def _ids_in(user):
    return [line.split('"id": "')[1].split('"')[0]
            for line in user.splitlines() if '"id": "' in line]


class TestTheAnswerKeyCheck(unittest.TestCase):
    """The one check whose failure is silent and expensive."""

    def test_a_phrase_carried_out_of_the_ending_is_caught(self):
        self.assertTrue(quality.repeats_the_ending(
            "The cardmember tries twice and is locked out after two failed attempts.",
            "Locked out after two failed attempts"))

    def test_sharing_the_subject_matter_is_not_a_leak(self):
        """A description of a dispute and an ending about a dispute share words honestly. A check
        that fired on that would flag most of the scenario space and be switched off within a week."""
        self.assertFalse(quality.repeats_the_ending(
            "The cardmember raises a dispute about a charge they do not recognise.",
            "The dispute is filed and a reference number is given"))

    def test_two_words_in_common_is_not_enough(self):
        self.assertFalse(quality.repeats_the_ending(
            "The dispute is raised by somebody who is not the cardholder.",
            "The dispute is filed and a reference number is given"))

    def test_an_empty_ending_can_never_be_leaked(self):
        self.assertFalse(quality.repeats_the_ending("Anything at all.", ""))

    def test_it_is_judged_against_this_scenario_own_ending(self):
        """Two scenarios can be written identically and only one of them be leaking, because a
        leak is a statement of where *this* route finishes."""
        happy, locked = build_scenario_space(_INTAKE, with_probes=False)
        text = "The cardmember tries and is locked out after two failed attempts."
        happy.description = locked.description = text
        self.assertEqual(quality.leaking([happy, locked]), [locked])


class TestWhatElseTheAuditCatches(unittest.TestCase):
    def _scenario(self, **overrides):
        scenario = build_scenario_space(_INTAKE, with_probes=False)[0]
        for key, value in overrides.items():
            setattr(scenario, key, value)
        return scenario

    def test_a_missing_name_is_a_problem(self):
        self.assertIn("name is missing.", quality.problems(self._scenario(name="")))

    def test_a_fragment_of_a_description_is_a_problem(self):
        problems = quality.problems(self._scenario(description="It goes wrong."))
        self.assertTrue(any("characters" in p for p in problems), problems)

    def test_a_well_written_scenario_has_nothing_to_say_about_it(self):
        self.assertEqual(quality.problems(self._scenario(**_GOOD)), [])


class TestTheWriterRewritesOnlyWhatFailed(unittest.TestCase):
    def setUp(self):
        self.scenarios = build_scenario_space(_INTAKE, with_probes=False)
        self.calls = []

    def _writer(self, first_reply, repair_reply=_GOOD):
        def complete(system, user, **kwargs):
            # The rewrite is chunked like the writing, so it answers in the same shape: a map
            # keyed by id, not one object standing for one scenario.
            if "SCENARIOS TO WRITE AGAIN" in user:
                self.calls.append("repair")
                return json.dumps({i: dict(repair_reply) for i in _ids_in(user)})
            self.calls.append("write")
            return json.dumps({i: dict(first_reply) for i in _ids_in(user)})
        return ScenarioWriter(complete=complete)

    def test_nothing_is_rewritten_when_nothing_is_wrong(self):
        self._writer(_GOOD).write(self.scenarios, _INTAKE)
        self.assertEqual(self.calls, ["write"])

    def test_the_rewrites_are_chunked_rather_than_one_call_each(self):
        """It used to be a call per broken scenario. On a space where the audit catches a
        systematic habit that is a call for every scenario in the run, at the price of the whole
        pass again -- and nothing in one rewrite depends on another."""
        leak = dict(_GOOD, description="The cardmember tries and is locked out after two failed "
                                       "attempts, which ends the conversation.")
        writer = self._writer(leak)
        writer._batch = 50
        writer.write(self.scenarios, _INTAKE)
        self.assertEqual(self.calls.count("repair"), 1,
                         "one chunk of rewrites should be one call")

    def test_a_leaking_scenario_is_rewritten(self):
        leak = dict(_GOOD, description="The cardmember tries and is locked out after two failed "
                                       "attempts, which ends the conversation.")
        self._writer(leak).write(self.scenarios, _INTAKE)
        self.assertIn("repair", self.calls)
        self.assertEqual(quality.leaking(self.scenarios), [])

    def test_a_scenario_that_was_fine_is_left_alone(self):
        """Only the leaking one goes back. The other is written the same way and is not leaking,
        because the phrase is not its ending."""
        leak = dict(_GOOD, description="The cardmember tries and is locked out after two failed "
                                       "attempts, which ends the conversation.")
        self._writer(leak).write(self.scenarios, _INTAKE)
        happy = next(s for s in self.scenarios if "reference number" in s.termination)
        self.assertIn("locked out", happy.description)

    def test_a_rewrite_that_is_not_cleaner_is_thrown_away(self):
        """A second attempt that fixes one fault and introduces another is not an improvement,
        and the text already there at least came from a call that saw the whole chunk."""
        leak = dict(_GOOD, description="The cardmember tries and is locked out after two failed "
                                       "attempts, which ends the conversation.")
        worse = {"name": "", "description": "", "turn_plan": ""}
        self._writer(leak, repair_reply=worse).write(self.scenarios, _INTAKE)

        locked = next(s for s in self.scenarios if "Locked out" in s.termination)
        self.assertTrue(locked.description, "the worse rewrite replaced the original")
        self.assertTrue(locked.name)

    def test_a_rewrite_that_never_comes_back_leaves_the_text_alone(self):
        def complete(system, user, **kwargs):
            if "ONE SCENARIO TO WRITE AGAIN" in user:
                raise RuntimeError("gateway down")
            return json.dumps({i: dict(_GOOD, name="") for i in _ids_in(user)})

        ScenarioWriter(complete=complete).write(self.scenarios, _INTAKE)
        for scenario in self.scenarios:
            self.assertTrue(scenario.description)


class TestSettlingADisagreement(unittest.TestCase):
    """A third opinion, and only where one is worth paying for."""

    def setUp(self):
        self.scenarios = build_scenario_space(_INTAKE, with_probes=False)
        for scenario in self.scenarios:
            scenario.materiality = "Low"
            scenario.materiality_rationale = "Nothing much turns on it."

    def _review(self, review_tier, verdict="High"):
        seen = {"adjudicated": []}

        def complete(system, user, **kwargs):
            if "TWO READINGS THAT DO NOT AGREE" in user:
                ids = _ids_in(user)
                seen["adjudicated"] += ids
                return json.dumps({i: {"materiality": verdict, "rationale": "The first reading "
                                                                           "missed the money."}
                                   for i in ids})
            if "proposals" in user.lower():
                return json.dumps({"proposals": []})
            return json.dumps({i: {"materiality": review_tier, "rationale": "r", "flag": ""}
                               for i in _ids_in(user)})

        ScenarioReviewer(complete=complete).review(self.scenarios, _INTAKE)
        return seen

    def test_agreement_costs_nothing(self):
        self.assertEqual(self._review("Low")["adjudicated"], [])

    def test_one_tier_apart_is_still_agreement(self):
        """One tier is two readings agreeing to within the precision the scale has."""
        self.assertEqual(self._review("Medium")["adjudicated"], [])

    def test_two_tiers_apart_is_settled(self):
        seen = self._review("High")
        self.assertEqual(sorted(seen["adjudicated"]), sorted(s.id for s in self.scenarios))

    def test_the_verdict_lands_in_the_reviews_own_column(self):
        self._review("High", verdict="High")
        for scenario in self.scenarios:
            self.assertEqual(scenario.review_materiality, "High")
            self.assertEqual(scenario.materiality, "Low", "the first reading was overwritten")
            self.assertIn("missed the money", scenario.review_rationale)

    def test_the_gap_is_measured_in_tiers_not_in_names(self):
        scenario = self.scenarios[0]
        scenario.materiality, scenario.review_materiality = "Low", "High"
        self.assertEqual(_tiers_apart(scenario), 2)
        self.assertGreaterEqual(_tiers_apart(scenario), ADJUDICATE_GAP)

    def test_a_review_that_said_nothing_is_not_a_disagreement(self):
        scenario = self.scenarios[0]
        scenario.materiality, scenario.review_materiality = "Low", ""
        self.assertEqual(_tiers_apart(scenario), 0)


if __name__ == "__main__":
    unittest.main()
