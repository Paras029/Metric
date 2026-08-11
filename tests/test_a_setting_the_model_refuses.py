"""A generation parameter outside what the model accepts, said as one line rather than a body.

Raising the judgement tier's output cap produced this, several levels inside a nested JSON body:

    Error code: 400 - {'reason': 'Model invocation failed. Reason: Could not contact endpoint.
    Non-200 HTTP status code: 400 All retries exhausted.', 'response_error': [{'error': {'code':
    400, 'message': 'Unable to submit request because it has a maxOutputTokens value of 256000 but
    the supported range is from 1 (inclusive)to 65537 (exclusive). ...

Everything a person needs is in there and none of it is where they are looking. Worse, the outer
wrapper says "Could not contact endpoint" and "All retries exhausted", which reads as a gateway
that was down -- so the natural next move is to run it again, and it fails identically, on every
call, for the whole run.

It is a configuration fault, and the two things worth saying about one are which setting of ours
to change and that retrying will not help. Both are recoverable from the provider's own wording,
including the ceiling, so the plain sentence is built from what the gateway actually said rather
than from anything assumed about the model.

The other half of this is restraint. Anything not recognised is passed through untouched: a
misleading label on a real fault costs more than a raw error message ever does.
"""
import unittest

from scenario_generator.llm import config
from scenario_generator.llm.gateway import SettingRejected, _explain

_REAL = ("Error code: 400 - {'reason': 'Model invocation failed. Reason: Could not contact "
         "endpoint. Non-200 HTTP status code: 400 All retries exhausted.', 'response_error': "
         "[{'error': {'code': 400, 'message': 'Unable to submit request because it has a "
         "maxOutputTokens value of 256000 but the supported range is from 1 (inclusive)"
         "to 65537 (exclusive). Update the value and try again.', 'status': 'INVALID_ARGUMENT'}}]}")

_JUDGEMENT = config.Tier(name="judgement", model="a-model", max_tokens=256_000,
                         reasoning_effort="high", max_attempts=4)


def _said(text, tier=_JUDGEMENT, max_tokens=None):
    return str(_explain(RuntimeError(text), tier, max_tokens))


class TestWhatItSaysAboutTheRealFailure(unittest.TestCase):
    def test_it_is_a_configuration_fault_rather_than_a_gateway_one(self):
        self.assertIsInstance(_explain(RuntimeError(_REAL), _JUDGEMENT, None), SettingRejected)

    def test_it_names_what_was_asked_for(self):
        self.assertIn("256,000 tokens", _said(_REAL))

    def test_it_recovers_the_ceiling_from_the_gateways_own_wording(self):
        """The range is stated exclusive, so the usable ceiling is one below it. Read rather than
        assumed: what a model accepts is not something this package can know."""
        self.assertIn("ceiling is 65,536", _said(_REAL))

    def test_it_names_both_places_the_setting_can_be_changed(self):
        message = _said(_REAL)
        self.assertIn("LLM_JUDGEMENT_MAX_TOKENS", message)
        self.assertIn("tiers.judgement.max_tokens", message)

    def test_it_says_retrying_will_not_help(self):
        """The wrapper says "All retries exhausted", which reads as a gateway that was down."""
        self.assertIn("Retrying will not help", _said(_REAL))

    def test_the_gateways_own_words_are_kept_underneath(self):
        """A translation that replaced the original would take the evidence with it."""
        self.assertIn("INVALID_ARGUMENT", _said(_REAL))

    def test_the_actionable_line_comes_first(self):
        """It is logged with %s, so whatever leads is what a person reads."""
        self.assertTrue(_said(_REAL).startswith("The model refused the output cap"))


class TestItNamesTheRightTier(unittest.TestCase):
    def test_the_tier_whose_call_failed(self):
        materiality = config.Tier(name="materiality", model="m", max_tokens=99_000,
                                  reasoning_effort="medium", max_attempts=2)
        self.assertIn("LLM_MATERIALITY_MAX_TOKENS", _said(_REAL, tier=materiality))

    def test_a_per_stage_override_points_at_its_own_tier(self):
        """A stage tier is named "judgement:intake_draft"; the setting to change is the tier's."""
        staged = config.Tier(name="judgement:intake_draft", model="m", max_tokens=99_000,
                             reasoning_effort="high", max_attempts=4)
        self.assertIn("LLM_JUDGEMENT_MAX_TOKENS", _said(_REAL, tier=staged))

    def test_a_call_overriding_the_cap_directly_reports_that_number(self):
        self.assertIn("120,000 tokens", _said(_REAL, max_tokens=120_000))

    def test_it_still_says_something_useful_with_no_tier_at_all(self):
        message = _said(_REAL, tier=None)
        self.assertIn("max_tokens for this call", message)


class TestWhatItLeavesAlone(unittest.TestCase):
    """A misleading label on a real fault costs more than a raw error message ever does."""

    def test_a_busy_gateway_is_not_relabelled(self):
        busy = RuntimeError("Error code: 503 - service unavailable, please retry")
        self.assertNotIsInstance(_explain(busy, _JUDGEMENT, None), SettingRejected)
        self.assertIs(_explain(busy, _JUDGEMENT, None).__class__, RuntimeError)

    def test_a_rejected_model_name_is_not_relabelled(self):
        wrong = RuntimeError("Error code: 404 - model 'typo-model' was not found")
        self.assertNotIsInstance(_explain(wrong, _JUDGEMENT, None), SettingRejected)

    def test_a_content_filter_is_not_relabelled(self):
        filtered = RuntimeError("Error code: 400 - the request was blocked by a content filter")
        self.assertNotIsInstance(_explain(filtered, _JUDGEMENT, None), SettingRejected)

    def test_an_unrecognised_failure_comes_back_as_the_same_object(self):
        """Not a copy: the traceback and anything a caller checks on it have to survive."""
        original = RuntimeError("something else entirely")
        self.assertIs(_explain(original, _JUDGEMENT, None), original)


class TestOtherWordingsForTheSameFault(unittest.TestCase):
    """The exact phrasing belongs to the provider and is theirs to change. Missing one costs only
    the plain sentence, so these are matched loosely and on purpose."""

    def test_an_openai_style_rejection(self):
        message = _said("max_tokens must be less than or equal to 32768")
        self.assertIn("The model refused the output cap", message)

    def test_a_snake_case_output_token_parameter(self):
        message = _said("Invalid value for max_output_tokens: supported range is 1 to 8192")
        self.assertIn("The model refused the output cap", message)

    def test_a_wording_with_no_recoverable_ceiling_still_says_what_to_change(self):
        """Better to say which knob than to say nothing because a number could not be scraped."""
        message = _said("max_tokens exceeds the limit for this model")
        self.assertIn("LLM_JUDGEMENT_MAX_TOKENS", message)
        self.assertNotIn("ceiling is", message)


class TestAPassThatGotNothingDoesNotReportSuccess(unittest.TestCase):
    """The wiring issue behind "I can set a higher cap on the writer and it works".

    It does not work -- it fails on every call and says nothing. A batched pass reports a failure
    as an entry in its results rather than raising it, which is right for one dropped chunk and
    exactly wrong when every chunk failed: "leave it as the passes before it set it" then applies
    to the whole pass, so the stage completes, writes its workbook, and reports success with every
    scenario still carrying the deterministic placeholder text it was born with.

    That is how an output cap above the model's ceiling looked like a setting the writer accepted
    while the intake refused it. The intake makes one call, so its 400 was raised; the writer makes
    several, so its 400s became a wall of warnings and a green stage.
    """

    def _writer_against(self, complete):
        from scenario_generator.core.models import (Capability, Decision, IntakeData, Persona,
                                                    State, Tool)
        from scenario_generator.llm.writer import ScenarioWriter
        from scenario_generator.pipeline import build_scenario_space

        intake = IntakeData(
            use_case={"Use case name": "Disputes", "Business objective": "Resolve"},
            personas=[Persona("P1", "Cardmember", [], True)],
            capabilities=[Capability("CAP-01", "Identity", "Gating")],
            decisions=[Decision("DEC-01", "Identity", "CAP-01", "", ["Pass", "Fail"])],
            states=[State("S-00", "Start", "Start", ["DEC-01"], False),
                    State("S-01", "DEC-01=Pass", "Verified", [], True, "Happy path"),
                    State("S-02", "DEC-01=Fail", "Locked out", [], True, "Termination")],
            tools=[Tool("Identity service", "CAP-01", True)])
        scenarios = build_scenario_space(intake, with_probes=False)
        return ScenarioWriter(complete=complete), scenarios, intake

    def test_a_writer_refused_on_every_call_fails_instead_of_finishing(self):
        class Refusing:
            def __call__(self, system, user, **kwargs):
                raise RuntimeError(_REAL)

            def batch(self, system, user_messages, **kwargs):
                return [RuntimeError(_REAL) for _ in user_messages]

        writer, scenarios, intake = self._writer_against(Refusing())
        with self.assertRaises(Exception) as caught:
            writer.write(scenarios, intake)
        self.assertIn("maxOutputTokens", str(caught.exception))

    def test_the_scenarios_are_left_as_they_were_rather_than_half_written(self):
        class Refusing:
            def __call__(self, system, user, **kwargs):
                raise RuntimeError(_REAL)

            def batch(self, system, user_messages, **kwargs):
                return [RuntimeError(_REAL) for _ in user_messages]

        writer, scenarios, intake = self._writer_against(Refusing())
        before = [s.description for s in scenarios]
        with self.assertRaises(Exception):
            writer.write(scenarios, intake)
        self.assertEqual([s.description for s in scenarios], before)

    def test_one_failed_call_among_several_is_still_absorbed(self):
        """The property this must not break: a gateway that drops one chunk should not cost the
        other nine, and every caller already handles a chunk arriving unanswered."""
        import json

        good = json.dumps({"SC-001": {"name": "A handle", "description": "The cardmember "
                                      "disputes a charge they do not recognise at all.",
                                      "turn_plan": "1. Open."}})

        class Flaky:
            def __call__(self, system, user, **kwargs):
                raise RuntimeError("gateway down")

            def batch(self, system, user_messages, **kwargs):
                return [RuntimeError("gateway down") if index else good
                        for index, _ in enumerate(user_messages)]

        writer, scenarios, intake = self._writer_against(Flaky())
        writer.write(scenarios, intake)          # must not raise
        self.assertTrue(any("do not recognise" in s.description for s in scenarios))


class TestTheBatchPathExplainsItToo(unittest.TestCase):
    """Most of a run is batched, and a batch reports a failure as an entry rather than raising it.

    Explaining only the single-call path is what made this asymmetric in the first place: the
    intake makes one call and got a raised 400, the writer makes several and got the provider's
    raw nested body once per chunk. Same fault, two different readings of it.
    """

    def _batched(self, tier):
        from unittest import mock

        from scenario_generator.llm import gateway

        class Chain:
            def batch(self, inputs, config=None, return_exceptions=False):
                return [RuntimeError(_REAL) for _ in inputs]

            def __or__(self, other):
                return self

            def __ror__(self, other):
                return self

        with mock.patch.object(gateway, "chat_model", return_value=Chain()), \
             mock.patch.object(gateway, "_batch_prompt", return_value=Chain()):
            return gateway.ask_llm_batch("sys", ["a", "b"], tier=tier)

    def test_every_failed_entry_is_explained(self):
        replies = self._batched(_JUDGEMENT)
        self.assertEqual(len(replies), 2)
        for reply in replies:
            self.assertIsInstance(reply, SettingRejected)
            self.assertIn("The model refused the output cap", str(reply))

    def test_it_names_the_tier_that_batch_was_running_on(self):
        standard = config.Tier(name="standard", model="m", max_tokens=256_000,
                               reasoning_effort="minimal", max_attempts=4)
        self.assertIn("LLM_STANDARD_MAX_TOKENS", str(self._batched(standard)[0]))


class TestAStoppedPassIsNotAFailedOne(unittest.TestCase):
    """Every entry being an exception is also what a person pressing Stop produces, and a run
    doing what it was told is not a fault to raise."""

    def test_a_batch_of_stopped_entries_comes_back_rather_than_raising(self):
        import threading

        from scenario_generator.llm.calling import call_batch

        stop = threading.Event()
        stop.set()
        replies = call_batch(lambda s, u, **k: "never sent", "sys", ["a", "b"], cancel=stop)
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(isinstance(r, BaseException) for r in replies))


if __name__ == "__main__":
    unittest.main()
