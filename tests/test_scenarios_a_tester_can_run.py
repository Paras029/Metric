"""Under-specification, caught by code before it is issued.

The scenario text is the one output of this tool that leaves it. A description that names no
subject matter, or a turn plan that tells the tester to "provide the relevant details", is a test
two people run two different ways -- so the transcripts cannot be compared, which is the failure
the whole exercise is built to avoid. It also fails quietly: the pack looks complete.

Everything here is a *fact about the text* rather than a judgement about how good it is. A check
needing judgement would need a model, and a model checking a model's work on every scenario is the
expensive way to be no more certain. The judgement half is the review's, and it has its own flag.
"""
import unittest

from metric.domain.models import Persona, Scenario, TurnMeta
from metric.phases.scenario_generator.scenarios import quality


def _scenario(description="", turn_plan="", turns=1, name="A name", termination="") -> Scenario:
    return Scenario(
        id="SC-001", path=[], category="Happy path",
        persona=Persona("P1", "Cooperative cardmember", (), True),
        seeded_state="", termination=termination, capabilities=[], tools=[],
        touches_state_change=False,
        turn_meta=[TurnMeta(i, f"DEC-0{i}", "A decision", "Pass", "", "S-01")
                   for i in range(1, turns + 1)],
        name=name, description=description, turn_plan=turn_plan)


GOOD_DESCRIPTION = ("A verified account holder tries to submit a meter reading for a property "
                    "that has no reading recorded against it for the current billing period. The "
                    "route matters because the submission path is entered from an empty history.")
GOOD_PLAN = ("1. Open the conversation as the named account holder and give the account number "
             "and postcode when asked.\n2. Ask to submit a meter reading for a property with no "
             "reading recorded this period.\n3. Give a plausible reading figure and confirm the "
             "submission.")


class TestTheOrdinaryCaseIsQuiet(unittest.TestCase):
    """The first thing a check has to be is silent about text that is fine. Every one of these
    fires a repair call, and a check that fires on good work costs a call per scenario to change
    nothing."""

    def test_a_well_written_scenario_has_no_problems(self):
        self.assertEqual(quality.problems(
            _scenario(GOOD_DESCRIPTION, GOOD_PLAN, turns=3)), [])


class TestAPlanShorterThanTheRoute(unittest.TestCase):
    def test_missing_turns_are_caught(self):
        found = quality.problems(_scenario(GOOD_DESCRIPTION, GOOD_PLAN, turns=5))
        self.assertTrue(any("3 numbered lines where the route needs 5" in p for p in found), found)

    def test_a_longer_plan_is_not_a_fault(self):
        """More lines than turns has said too much, which is a style problem. Fewer has left part
        of the route with no instruction against it, and the tester cannot reach the situation."""
        self.assertEqual(quality.problems(_scenario(GOOD_DESCRIPTION, GOOD_PLAN, turns=2)), [])

    def test_an_unnumbered_plan_is_caught_by_the_same_check(self):
        found = quality.problems(_scenario(GOOD_DESCRIPTION, "Just do the whole thing.", turns=3))
        self.assertTrue(any("numbered lines" in p for p in found), found)


class TestAProbeIsHeldToItsOwnContract(unittest.TestCase):
    """A route is scripted turn by turn and its count is exact. A probe has no decision path, so
    its count is a floor and the prompt says so -- checking it against the route's number would
    report a plan the prompt asked for."""

    def _probe(self, turn_plan, steps):
        scenario = _scenario(GOOD_DESCRIPTION, turn_plan, turns=steps)
        scenario.probe_id = "NF-001"
        scenario.probe_family = "Repetition"
        scenario.origin = "probe"
        return scenario

    def test_more_lines_than_steps_is_what_a_probe_is_for(self):
        plan = "\n".join(f"{n}. Press the same request again, in the words a user would use."
                          for n in range(1, 7))
        self.assertEqual(quality.problems(self._probe(plan, steps=3)), [])

    def test_fewer_lines_than_the_floor_is_still_short(self):
        found = quality.problems(self._probe(
            "1. Press the same request again, in the words a user would use.", steps=4))
        self.assertTrue(any("needs 4" in p for p in found), found)


class TestALineATesterCannotAct(unittest.TestCase):
    def test_a_line_of_two_words_is_caught(self):
        found = quality.problems(_scenario(GOOD_DESCRIPTION, "1. Confirm.\n2. Continue.", turns=2))
        self.assertTrue(any("too short to act on" in p for p in found), found)

    def test_it_names_which_line(self):
        plan = ("1. Open the conversation as the named account holder and give the reference.\n"
                "2. Confirm.")
        found = quality.problems(_scenario(GOOD_DESCRIPTION, plan, turns=2))
        self.assertTrue(any("line 2" in p for p in found), found)


class TestStandingInForTheThingItShouldHaveNamed(unittest.TestCase):
    def test_relevant_details_is_not_an_instruction(self):
        plan = ("1. Open the conversation as the account holder and provide the relevant details "
                "when asked.\n2. Ask to change the booking to a later date.")
        found = quality.problems(_scenario(GOOD_DESCRIPTION, plan, turns=2))
        self.assertTrue(any("relevant details" in p for p in found), found)

    def test_the_description_may_still_say_appropriate(self):
        """A description may honestly say a condition is appropriate to the account. An
        instruction to *do* something appropriate is an instruction to guess."""
        description = ("The cardmember asks about a charge that is appropriate to their account "
                       "tier. The route matters because the tier is read before the charge is.")
        self.assertEqual(quality.problems(_scenario(description, GOOD_PLAN, turns=3)), [])


class TestTextLeftForSomebodyElse(unittest.TestCase):
    def test_a_placeholder_in_the_plan_is_caught(self):
        plan = ("1. Open the conversation and give reference [insert reference here].\n"
                "2. Ask to change the booking to a later date.\n3. Confirm the new date offered.")
        found = quality.problems(_scenario(GOOD_DESCRIPTION, plan, turns=3))
        self.assertTrue(any("left for" in p for p in found), found)

    def test_a_placeholder_in_the_description_is_caught(self):
        description = ("The cardmember disputes a charge of TBD on their statement. The route "
                       "matters because the amount decides which path is taken.")
        found = quality.problems(_scenario(description, GOOD_PLAN, turns=3))
        self.assertTrue(any("left for" in p for p in found), found)


class TestWhereTheLineIsDrawn(unittest.TestCase):
    """Every check here costs a repair call when it fires, so one that fires on sound text buys a
    call per scenario to change nothing. That is what keeps the bar at "unrunnable whatever else
    is around it" rather than at "shorter than the prompt asked for"."""

    def test_a_single_sentence_description_is_left_to_the_review(self):
        """The prompt asks for two or three. One that names the position, the condition and the
        subject matter is still a description, and judging that needs judgement."""
        description = ("The cardmember gets the security question wrong twice in a row while "
                       "raising a dispute about a charge they do not recognise.")
        self.assertEqual(quality.problems(_scenario(description, GOOD_PLAN, turns=3)), [])

    def test_a_fragment_is_still_caught(self):
        found = quality.problems(_scenario("Too short", GOOD_PLAN, turns=3))
        self.assertEqual(sum(1 for p in found if p.startswith("description")), 1, found)


class TestTheAnswerKeyCheckStillHolds(unittest.TestCase):
    """The check this module was built for. Nothing added here may weaken it."""

    def test_a_description_repeating_the_ending_is_still_caught(self):
        found = quality.problems(_scenario(
            "The cardmember fails the identity check twice and the account is locked out. The "
            "route matters because the second failure is what trips it.",
            GOOD_PLAN, turns=3, termination="The account is locked out"))
        self.assertTrue(any("states how the interaction ends" in p for p in found), found)


if __name__ == "__main__":
    unittest.main()
