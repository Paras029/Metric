"""Every setting in tuning.yml, proved to reach the thing it configures.

A settings file has one obligation and one characteristic failure. The obligation is that writing
a value in it changes what the tool does. The failure is that it silently does not -- the run looks
normal, produces plausible output, and quietly ignores everything asked of it, which is worse than
refusing to start because there is nothing to notice.

Three ways that happens, and one test class each.

*The file is never found.* A bare relative path resolves against the working directory, so a tool
launched from anywhere else reads no file at all and reverts every setting to its built-in default.

*The file is read once and cached.* An interface that runs for hours reads it at the first lookup
and then answers from memory, so an edit made while it is running has no effect whatsoever.

*A key is read but never used.* The value arrives at the accessor and stops there, never reaching
the call it was supposed to configure.

The last is what the exhaustive class below is for: it walks the shipped file key by key and, for
each, sets a distinctive value and asserts it comes back out at the call site.
"""
import logging
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scenario_generator.llm import config

_REPO_TUNING = Path(__file__).resolve().parent.parent / "tuning.yml"


class _Tuned(unittest.TestCase):
    """A test case that runs against a tuning file it writes."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.path = self.directory / "tuning.yml"
        self._environment = mock.patch.dict(os.environ, {"TUNING_PATH": str(self.path)})
        self._environment.start()
        self.addCleanup(self._environment.stop)
        self.addCleanup(config.reload_tuning)

    def write(self, mapping: dict) -> None:
        self.path.write_text(yaml.safe_dump(mapping), encoding="utf-8")
        config.reload_tuning()                             # same-tick writes need forcing


class TestTheFileIsFoundFromAnywhere(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config.reload_tuning)

    def test_it_is_found_from_a_directory_that_is_not_the_repository_root(self):
        """A relative path resolves against the working directory. Launched from anywhere else,
        the file is not found, every setting reverts to its default, and nothing says so."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TUNING_PATH", None)
            with mock.patch("pathlib.Path.cwd", return_value=Path(tempfile.mkdtemp())):
                config.reload_tuning()
                found = Path(config.tuning_path())

        self.assertTrue(found.is_file(), f"no tuning file found; looked at {found}")
        self.assertEqual(found.resolve(), _REPO_TUNING.resolve())

    def test_it_is_found_from_a_directory_inside_the_project(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TUNING_PATH", None)
            inside = _REPO_TUNING.parent / "scenario_generator"
            with mock.patch("pathlib.Path.cwd", return_value=inside):
                config.reload_tuning()
                found = Path(config.tuning_path())
        self.assertEqual(found.resolve(), _REPO_TUNING.resolve())

    def test_an_explicit_path_still_wins(self):
        elsewhere = Path(tempfile.mkdtemp()) / "mine.yml"
        elsewhere.write_text("concurrency: 11\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TUNING_PATH": str(elsewhere)}):
            config.reload_tuning()
            self.assertEqual(config.MAX_CONCURRENCY, 11)

    def test_a_missing_file_says_so_rather_than_quietly_defaulting(self):
        missing = Path(tempfile.mkdtemp()) / "nothing.yml"
        with mock.patch.dict(os.environ, {"TUNING_PATH": str(missing)}):
            config.reload_tuning()
            with self.assertLogs("scenario_generator.llm.config", level=logging.WARNING) as logged:
                config.tuning()
        self.assertIn("built-in default", "\n".join(logged.output))


class TestAnEditTakesEffectWithoutARestart(_Tuned):
    """The interface runs for hours against one process. A file read once at the first lookup and
    cached for the life of that process means every edit made while it is up does nothing."""

    def test_changing_a_value_changes_what_the_next_call_sees(self):
        self.write({"concurrency": 4, "stages": {"writer": {"batch_size": 8}}})
        self.assertEqual(config.MAX_CONCURRENCY, 4)
        self.assertEqual(config.stage_batch_size("WRITER", 99), 8)

        # Written directly, without reload_tuning: this is the point of the test.
        self.path.write_text(
            yaml.safe_dump({"concurrency": 8,
                            "stages": {"writer": {"batch_size": 32, "concurrency": 8}}}),
            encoding="utf-8")
        os.utime(self.path, ns=(0, 0))                     # a signature that cannot collide

        self.assertEqual(config.MAX_CONCURRENCY, 8)
        self.assertEqual(config.stage_batch_size("WRITER", 99), 32)
        self.assertEqual(config.stage_concurrency("WRITER"), 8)

    def test_removing_a_key_returns_it_to_its_default(self):
        self.write({"stages": {"writer": {"batch_size": 32}}})
        self.assertEqual(config.stage_batch_size("WRITER", 8), 32)

        self.path.write_text(yaml.safe_dump({"stages": {"writer": {}}}), encoding="utf-8")
        os.utime(self.path, ns=(0, 0))
        self.assertEqual(config.stage_batch_size("WRITER", 8), 8)


class TestEverySettingReachesItsCallSite(_Tuned):
    """The exhaustive one: every key the shipped file carries, set and read back."""

    def test_the_shipped_file_parses_and_has_the_shape_the_code_expects(self):
        loaded = yaml.safe_load(_REPO_TUNING.read_text(encoding="utf-8"))
        self.assertIsInstance(loaded, dict)
        self.assertEqual(sorted(loaded["stages"]),
                         sorted(key.lower() for key in config.STAGE_KEYS))
        for section in ("defaults", "tiers", "concurrency", "stages", "ingestion", "redaction"):
            self.assertIn(section, loaded)

    def test_the_loose_settings(self):
        self.write({
            "concurrency": 9,
            "defaults": {"temperature": 0.77, "max_attempts": 7},
            "ingestion": {"max_corpus_chars": 12345, "max_context_chars": 54321,
                          "resolve_passes": 5, "vision": False, "max_image_bytes": 999},
        })
        self.assertEqual(config.MAX_CONCURRENCY, 9)
        self.assertEqual(config.DEFAULT_TEMPERATURE, 0.77)
        self.assertEqual(config.DEFAULT_MAX_ATTEMPTS, 7)
        self.assertEqual(config.MAX_CORPUS_CHARS, 12345)
        self.assertEqual(config.MAX_CONTEXT_CHARS, 54321)
        self.assertEqual(config.INGEST_RESOLVE_PASSES, 5)
        self.assertEqual(config.LLM_VISION, False)
        self.assertEqual(config.MAX_IMAGE_BYTES, 999)

    def test_every_tier_field(self):
        self.write({"tiers": {
            "judgement": {"model": "j-model", "max_tokens": 111, "reasoning_effort": "low",
                          "max_attempts": 2, "temperature": 0.11},
            "materiality": {"model": "m-model", "max_tokens": 222, "reasoning_effort": "medium",
                            "max_attempts": 3, "temperature": 0.22},
            "standard": {"model": "s-model", "max_tokens": 333, "reasoning_effort": "high",
                         "max_attempts": 4, "temperature": 0.33},
        }})
        for tier, name, tokens, effort, attempts, temperature in (
                (config.JUDGEMENT, "j-model", 111, "low", 2, 0.11),
                (config.MATERIALITY, "m-model", 222, "medium", 3, 0.22),
                (config.STANDARD, "s-model", 333, "high", 4, 0.33)):
            self.assertEqual(tier.model, name)
            self.assertEqual(tier.max_tokens, tokens)
            self.assertEqual(tier.reasoning_effort, effort)
            self.assertEqual(tier.max_attempts, attempts)
            self.assertEqual(tier.temperature, temperature)

    def test_every_field_of_every_stage(self):
        """Thirteen call sites, six fields each. A stage whose entry is read but never consulted
        is the failure this rules out, and it can only be ruled out one stage at a time."""
        stages = {}
        for index, key in enumerate(config.STAGE_KEYS):
            stages[key.lower()] = {
                "model": f"model-for-{key.lower()}",
                "max_tokens": 1000 + index,
                "temperature": round(0.01 * (index + 1), 2),
                "reasoning_effort": "low",
                "max_attempts": 1 + index,
                "batch_size": 40 + index,
                "concurrency": 20 + index,
            }
        self.write({"stages": stages})

        for index, key in enumerate(config.STAGE_KEYS):
            tier = config.stage_tier(key, config.JUDGEMENT)
            self.assertEqual(tier.model, f"model-for-{key.lower()}", key)
            self.assertEqual(tier.max_tokens, 1000 + index, key)
            self.assertEqual(tier.temperature, round(0.01 * (index + 1), 2), key)
            self.assertEqual(tier.reasoning_effort, "low", key)
            self.assertEqual(tier.max_attempts, 1 + index, key)
            self.assertEqual(config.stage_batch_size(key, 8), 40 + index, key)
            self.assertEqual(config.stage_concurrency(key), 20 + index, key)

    def test_a_stage_falls_back_to_its_tier_and_then_to_the_master_model(self):
        self.write({"tiers": {"standard": {"model": "tier-model", "max_tokens": 500}},
                    "stages": {"writer": {}}})
        tier = config.stage_tier("WRITER", config.STANDARD)
        self.assertEqual(tier.model, "tier-model")
        self.assertEqual(tier.max_tokens, 500)

        self.write({"stages": {"writer": {}}})
        with mock.patch.dict(os.environ, {"LLM_MODEL_ID": "master-model"}):
            self.assertEqual(config.stage_tier("WRITER", config.STANDARD).model, "master-model")

    def test_every_redaction_field(self):
        self.write({"redaction": {
            "enabled": True, "mode": "substitution", "replacement_text": "[GONE]",
            "sensitivity": "strict", "exclude_entities": ["PERSON"],
            "allow": ["American Express"], "thresholds": {"secondary_pii_email": 0.7},
        }})
        self.assertTrue(config.PII_REDACTION)
        self.assertEqual(config.PII_REDACTION_MODE, "substitution")
        self.assertEqual(config.PII_REDACTION_REPLACEMENT_TEXT, "[GONE]")
        self.assertEqual(config.PII_REDACTION_SENSITIVITY, "strict")
        self.assertEqual(config.PII_REDACTION_EXCLUDE_ENTITIES, ["PERSON"])
        self.assertEqual(config.PII_REDACTION_ALLOW, ["American Express"])
        # Reshaped into what the redaction engine takes, rather than passed through as written.
        self.assertEqual(config.PII_REDACTION_THRESHOLDS,
                         {"secondary_pii_email": {"score": 0.7}})

    def test_the_environment_wins_over_the_file(self):
        """The whole reason both exist: one setting overridden for one run without editing a file
        a team shares."""
        self.write({"concurrency": 4, "stages": {"writer": {"batch_size": 8}}})
        with mock.patch.dict(os.environ, {"LLM_MAX_CONCURRENCY": "16",
                                          "LLM_STAGE_WRITER_BATCH_SIZE": "64"}):
            self.assertEqual(config.MAX_CONCURRENCY, 16)
            self.assertEqual(config.stage_batch_size("WRITER", 8), 64)


class TestABrokenFileNeverDegradesToDefaults(_Tuned):
    """The worst thing a settings file can do is parse-fail and be ignored.

    Every value it was setting reverts, the run proceeds, the output looks plausible, and the only
    sign is one line in a log. A model choice and a batch size go back to their defaults together,
    and the scenario space that comes out is not the one that was asked for.
    """

    #: The shape that broke a real file: another key alongside a bare flow mapping.
    BROKEN = textwrap.dedent("""
        stages:
          ingest_read:
            max_attempts: 4
            {}
          ingest_resolve:
            max_attempts: 4
        """)

    def test_a_broken_file_raises_on_the_first_read_rather_than_returning_defaults(self):
        self.path.write_text(self.BROKEN, encoding="utf-8")
        config.reload_tuning()
        with self.assertRaises(config.BrokenTuningFile):
            config.tuning()

    def test_the_message_names_the_file_and_the_line(self):
        self.path.write_text(self.BROKEN, encoding="utf-8")
        config.reload_tuning()
        try:
            config.tuning()
        except config.BrokenTuningFile as exc:
            message = str(exc)
        self.assertIn(str(self.path), message)
        self.assertIn("line", message)

    def test_startup_refuses_rather_than_running_on_settings_nobody_chose(self):
        self.path.write_text(self.BROKEN, encoding="utf-8")
        config.reload_tuning()
        with self.assertRaises(config.BrokenTuningFile):
            config.check_tuning()

    def test_breaking_a_working_file_mid_run_keeps_the_settings_that_were_working(self):
        """A run already under way must not change what it is doing because somebody mistyped a
        line in another window -- and must certainly not revert to defaults."""
        self.write({"concurrency": 8, "stages": {"writer": {"batch_size": 32}}})
        self.assertEqual(config.MAX_CONCURRENCY, 8)

        self.path.write_text(self.BROKEN, encoding="utf-8")
        os.utime(self.path, ns=(0, 0))

        with self.assertLogs("scenario_generator.llm.config", level=logging.ERROR) as logged:
            self.assertEqual(config.MAX_CONCURRENCY, 8)
        self.assertIn("still in force", "\n".join(logged.output))
        self.assertEqual(config.stage_batch_size("WRITER", 99), 32)

    def test_a_file_that_is_not_a_mapping_is_refused_too(self):
        self.path.write_text("- one\n- two\n", encoding="utf-8")
        config.reload_tuning()
        with self.assertRaises(config.BrokenTuningFile):
            config.tuning()

    def test_an_empty_file_is_fine_and_means_defaults(self):
        self.path.write_text("", encoding="utf-8")
        config.reload_tuning()
        self.assertEqual(config.tuning(), {})


class TestTheShippedFileCannotBeBrokenByAnOrdinaryEdit(unittest.TestCase):
    """The trap that produced the broken file above: a placeholder that stops being valid the
    moment somebody adds a line beside it. Ordinary edits have to stay ordinary."""

    def setUp(self):
        self.text = _REPO_TUNING.read_text(encoding="utf-8")

    def test_it_carries_no_bare_flow_mappings(self):
        offenders = [number for number, line in enumerate(self.text.splitlines(), start=1)
                     if line.strip() == "{}"]
        self.assertEqual(offenders, [], f"bare '{{}}' on line(s) {offenders} breaks on any edit")

    def test_every_stage_already_has_a_real_setting_to_add_a_line_beside(self):
        """An empty mapping cannot be extended with a block-style line. A stage entry that already
        has one key can always take another, which is the whole of what editing this file is."""
        loaded = yaml.safe_load(self.text)
        for name, entry in loaded["stages"].items():
            self.assertIsInstance(entry, dict, name)
            self.assertTrue(entry, f"stage '{name}' is empty; adding a line under it breaks it")

    def test_adding_a_model_line_to_every_stage_leaves_it_valid(self):
        """The single most likely edit, applied to each stage in turn."""
        lines = self.text.splitlines()
        for number, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("# model:"):
                continue
            edited = list(lines)
            edited[number] = line.replace("# model:", "model:", 1)
            try:
                yaml.safe_load("\n".join(edited))
            except Exception as exc:
                self.fail(f"uncommenting line {number + 1} breaks the file: {exc}")

    def test_changing_a_number_in_every_stage_leaves_it_valid(self):
        lines = self.text.splitlines()
        for number, line in enumerate(lines):
            if not any(key in line for key in ("batch_size:", "concurrency:", "max_attempts:")):
                continue
            if line.strip().startswith("#"):
                continue
            edited = list(lines)
            edited[number] = line.split(":")[0] + ": 99"
            try:
                yaml.safe_load("\n".join(edited))
            except Exception as exc:
                self.fail(f"editing line {number + 1} breaks the file: {exc}")


class TestTheConfiguredNumbersAreWhatTheCallsActuallyUse(_Tuned):
    """Reading the setting back is not the same as the pass obeying it. This runs the writer."""

    def _writer_run(self, scenario_count: int):
        from scenario_generator.core.models import IntakeData, Persona, Scenario
        from scenario_generator.llm import ScenarioWriter

        persona = Persona("P1", "Default", ["x"], True)
        intake = IntakeData(use_case={"Use case name": "X"}, personas=[persona],
                            capabilities=[], decisions=[], states=[], tools=[])
        # Written well enough to pass the audit that follows the writing, so what this counts is
        # the batching the tuning file asked for rather than the repair pass a bad reply triggers.
        scenarios = [Scenario(id=f"SC-{n:03d}", path=[], category="Happy path", persona=persona,
                              seeded_state="S-00", termination="S-01", capabilities=[], tools=[],
                              touches_state_change=False,
                              name=f"Handle {n}",
                              description="A situation the tester sets up, said at enough length "
                                          "to read as a description rather than a fragment.",
                              turn_plan="1. Open the conversation as the account holder and "
                                        "give the reference when asked.")
                     for n in range(scenario_count)]

        seen = {"waves": [], "messages": 0}

        def complete(system, user, **kwargs):
            return "{}"

        def batch(system, messages, max_concurrency=None, **kwargs):
            seen["messages"] += len(messages)
            seen["waves"].append(max_concurrency)
            return ["{}"] * len(messages)

        complete.batch = batch
        ScenarioWriter(complete=complete).write(scenarios, intake)
        return seen

    def test_the_configured_batch_size_decides_how_many_scenarios_go_in_one_call(self):
        self.write({"stages": {"writer": {"batch_size": 32}}})
        seen = self._writer_run(96)
        self.assertEqual(seen["messages"], 3, "96 scenarios at 32 a call is 3 calls")

        self.write({"stages": {"writer": {"batch_size": 8}}})
        seen = self._writer_run(96)
        self.assertEqual(seen["messages"], 12, "96 scenarios at 8 a call is 12 calls")

    def test_the_configured_concurrency_reaches_the_gateway(self):
        self.write({"concurrency": 4, "stages": {"writer": {"concurrency": 8}}})
        self.assertEqual(self._writer_run(96)["waves"], [8])

    def test_a_stage_with_no_concurrency_of_its_own_takes_the_global_cap(self):
        self.write({"concurrency": 6, "stages": {"writer": {}}})
        self.assertEqual(self._writer_run(96)["waves"], [6])

    def test_the_gateway_sends_in_waves_of_the_configured_size(self):
        """One level below the pass: what ask_llm_batch does with the number it is handed."""
        from scenario_generator.llm import gateway

        sent = []

        class _Chain:
            def batch(self, inputs, config=None, return_exceptions=False):
                sent.append(len(inputs))
                return ["{}"] * len(inputs)

            def __ror__(self, other):
                return self

            def __or__(self, other):
                return self

        with mock.patch.object(gateway, "_batch_prompt", lambda: _Chain()), \
                mock.patch.object(gateway, "chat_model", lambda **kwargs: _Chain()):
            gateway.ask_llm_batch("S", [f"m{n}" for n in range(20)], max_concurrency=8)

        self.assertEqual(sent, [8, 8, 4], "twenty messages at eight in flight is 8, 8, 4")


if __name__ == "__main__":
    unittest.main()
