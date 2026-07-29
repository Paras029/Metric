"""Which model tier each pass runs on.

The tiers exist because the work differs, not to economise for its own sake. Mapping free text
onto a closed vocabulary is classification; reading sixty pages and justifying a verdict is not,
and giving the first the second's budget makes it slower without making it better.

These tests pin the assignment. The failure they guard against is quiet in both directions: a
judgement pass silently demoted to a small model produces plausible output that is worse in ways
nobody notices, and a mechanical pass left on the large one costs time on every run forever.
"""
import os
import unittest
from unittest import mock

from scenario_generator.core.models import Decision, IntakeData, Persona, State, Tool
from scenario_generator.core.probes import build_probes
from scenario_generator.llm import config
from scenario_generator.llm.extractor import MetadataExtractor
from scenario_generator.llm.materiality import MaterialityAssessor
from scenario_generator.llm.reviewer import ScenarioReviewer
from scenario_generator.llm.writer import ScenarioWriter

_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default user", [], True)],
    capabilities=[],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass"])],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False)],
    tools=[Tool("Filing API", "CAP-01", True)],
)


class _Recorder:
    """Stands in for the gateway and remembers which tier each call asked for."""

    def __init__(self, reply="{}"):
        self.reply = reply
        self.tiers = []

    def __call__(self, system, user, tier=None, max_tokens=None, reasoning_effort=None,
                 model=None, **kwargs):
        self.tiers.append(tier)
        return self.reply


class TestTierDefinitions(unittest.TestCase):
    def test_the_tiers_are_ordered_by_how_much_room_the_work_needs(self):
        self.assertGreater(config.JUDGEMENT.max_tokens, config.MATERIALITY.max_tokens)
        self.assertGreaterEqual(config.MATERIALITY.max_tokens, config.STANDARD.max_tokens)
        self.assertGreaterEqual(config.STANDARD.max_tokens, config.FAST.max_tokens)

    def test_only_the_judgement_tier_reasons_at_length(self):
        self.assertEqual(config.JUDGEMENT.reasoning_effort, "high")
        self.assertEqual(config.STANDARD.reasoning_effort, "minimal")
        self.assertEqual(config.FAST.reasoning_effort, "minimal")

    def test_every_tier_falls_back_to_the_main_model(self):
        """Nothing changes until a smaller model is configured, which matters where a new model
        has to clear an approval before it can be used."""
        for tier in (config.JUDGEMENT, config.MATERIALITY, config.STANDARD, config.FAST):
            self.assertTrue(tier.model)

    def test_materiality_retries_less_than_the_default(self):
        """Every chunk of the benchmark hits this tier at once, so a busy gateway means several
        simultaneous retries rather than one -- this tier is given a shorter ladder for that."""
        self.assertLess(config.MATERIALITY.max_attempts, config.DEFAULT_MAX_ATTEMPTS)
        self.assertEqual(config.JUDGEMENT.max_attempts, config.DEFAULT_MAX_ATTEMPTS)

    def test_the_corpus_limit_leaves_room_for_the_prompt_and_the_reply(self):
        """Four characters to a token, against a million-token input window."""
        self.assertLess(config.MAX_CORPUS_CHARS / 4, 1_000_000)


def _same_settings(tier, base) -> bool:
    """Same model, budget, effort and retry ladder as ``base``.

    Not identity and not full equality: every call site now runs through
    :func:`config.stage_tier`, which always returns a freshly built ``Tier`` named for its own
    call site (e.g. ``"judgement:reviewer_propose"``) even where nothing overrides it -- the name
    is what makes an override visible in a log line. What these tests actually guard is that nothing
    silently changed the substance of which tier a pass runs on, which is every field but the name.
    """
    return (tier.model == base.model and tier.max_tokens == base.max_tokens
            and tier.reasoning_effort == base.reasoning_effort
            and tier.max_attempts == base.max_attempts)


class TestStageOverrideCascade(unittest.TestCase):
    """config.stage_tier / config.stage_batch_size: stage, then tier, then master.

    Nothing here is used by a real pass -- these pin the cascade mechanism itself, independent of
    which stage key a given pass happens to use, so the guarantee stays checked even if a call
    site's key is renamed.
    """

    def test_a_stage_with_no_override_matches_its_tier_exactly(self):
        tier = config.stage_tier("A_STAGE_NOBODY_OVERRIDES", config.JUDGEMENT)
        self.assertEqual(tier.model, config.JUDGEMENT.model)
        self.assertEqual(tier.max_tokens, config.JUDGEMENT.max_tokens)
        self.assertEqual(tier.reasoning_effort, config.JUDGEMENT.reasoning_effort)
        self.assertEqual(tier.max_attempts, config.JUDGEMENT.max_attempts)
        self.assertEqual(tier.temperature, config.JUDGEMENT.temperature)

    def test_a_stage_override_wins_over_its_tier(self):
        with mock.patch.dict(os.environ, {"LLM_STAGE_TEST_STAGE_MODEL_ID": "tiny-model"}):
            tier = config.stage_tier("TEST_STAGE", config.JUDGEMENT)
        self.assertEqual(tier.model, "tiny-model")

    def test_overriding_one_field_leaves_the_rest_on_the_tier(self):
        with mock.patch.dict(os.environ, {"LLM_STAGE_TEST_STAGE_TEMPERATURE": "0.9"}):
            tier = config.stage_tier("TEST_STAGE", config.MATERIALITY)
        self.assertEqual(tier.temperature, 0.9)
        self.assertEqual(tier.model, config.MATERIALITY.model)
        self.assertEqual(tier.max_tokens, config.MATERIALITY.max_tokens)
        self.assertEqual(tier.max_attempts, config.MATERIALITY.max_attempts)

    def test_an_override_on_one_stage_does_not_leak_into_another(self):
        with mock.patch.dict(os.environ, {"LLM_STAGE_TEST_STAGE_MODEL_ID": "tiny-model"}):
            other = config.stage_tier("A_DIFFERENT_STAGE", config.JUDGEMENT)
        self.assertEqual(other.model, config.JUDGEMENT.model)

    def test_batch_size_falls_back_to_the_default_unset(self):
        self.assertEqual(config.stage_batch_size("A_STAGE_NOBODY_OVERRIDES", 6), 6)

    def test_batch_size_override_wins(self):
        with mock.patch.dict(os.environ, {"LLM_STAGE_TEST_STAGE_BATCH_SIZE": "3"}):
            self.assertEqual(config.stage_batch_size("TEST_STAGE", 6), 3)

    def test_tier_level_temperature_override_is_read_by_the_gateway(self):
        """A tier that sets its own temperature is what chat_model actually bills for -- see
        gateway._generation_parameters, which now reads tier.temperature rather than always
        falling straight through to the global default."""
        from scenario_generator.llm.gateway import _generation_parameters

        with mock.patch.dict(os.environ, {"LLM_JUDGEMENT_TEMPERATURE": "0.9"}):
            tier = config._tier("judgement", "LLM_JUDGEMENT", 65_536, "high")
        parameters = _generation_parameters(tier, None, None, None)
        self.assertEqual(parameters["temperature"], 0.9)


class TestWhichPassUsesWhichTier(unittest.TestCase):
    def test_the_review_pass_reasons(self):
        recorder = _Recorder('{"proposals": []}')
        ScenarioReviewer(complete=recorder, batch_size=4).review(
            build_probes(_INTAKE)[:4], _INTAKE)
        self.assertTrue(recorder.tiers)
        self.assertTrue(all(_same_settings(t, config.JUDGEMENT) for t in recorder.tiers))

    def test_the_materiality_pass_runs_on_its_own_tier(self):
        recorder = _Recorder()
        MaterialityAssessor(complete=recorder).assess(build_probes(_INTAKE)[:4], _INTAKE)
        self.assertTrue(recorder.tiers)
        self.assertTrue(all(_same_settings(t, config.MATERIALITY) for t in recorder.tiers))

    def test_writing_scenario_text_stays_on_the_standard_tier(self):
        """Mechanical, but the modelling team reads it, so not the cheapest model."""
        recorder = _Recorder()
        ScenarioWriter(complete=recorder).write(build_probes(_INTAKE)[:4], _INTAKE)
        self.assertTrue(recorder.tiers)
        self.assertTrue(all(_same_settings(t, config.STANDARD) for t in recorder.tiers))

    def test_mapping_owner_scenarios_runs_on_the_fast_tier(self):
        """Classification against a closed list, with validation discarding anything outside it."""
        from scenario_generator.core.models import OwnerScenario

        recorder = _Recorder()
        MetadataExtractor(complete=recorder).extract(
            [OwnerScenario("OS-1", "The user authenticates.", "")], _INTAKE)
        self.assertTrue(recorder.tiers)
        self.assertTrue(all(_same_settings(t, config.FAST) for t in recorder.tiers))


if __name__ == "__main__":
    unittest.main()
