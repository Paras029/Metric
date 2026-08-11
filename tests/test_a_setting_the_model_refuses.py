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


if __name__ == "__main__":
    unittest.main()
