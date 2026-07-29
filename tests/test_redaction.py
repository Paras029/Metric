"""Redaction: off by default, a hard stop rather than a silent skip once it is on.

The engine itself -- ee_utils.redaction -- is an internal package not present in this repository
or its test environment, so it is stubbed here the same way SafeChain is stubbed in
test_gateway.py: a real module installed on sys.modules for the duration of a test, standing in
for the real contract (MaskingConfig, RedactionConfig, redact_text) rather than a loose mock.
"""
import sys
import types
import unittest
from unittest import mock

from scenario_generator.ingest.extraction import build_corpus
from scenario_generator.ingest.readers import Segment
from scenario_generator.ingest.redaction import RedactionUnavailable, redact_segments
from scenario_generator.llm import config


class _FakeMaskingConfig:
    def __init__(self, replacement_text=None):
        self.replacement_text = replacement_text


class _FakeRedactionConfig:
    def __init__(self, mode=None, masking=None, sensitivity=None, exclude_entities=None,
                 allow_list=None, thresholds=None):
        self.mode = mode
        self.masking = masking
        self.sensitivity = sensitivity
        self.exclude_entities = exclude_entities
        self.allow_list = allow_list
        self.thresholds = thresholds


class _NarrowRedactionConfig:
    """Stands in for an installed version that only understands mode and masking -- the two the
    integration guide actually showed being passed into RedactionConfig."""

    def __init__(self, mode=None, masking=None):
        self.mode = mode
        self.masking = masking


class _FakeResult:
    def __init__(self, text, mapping):
        self.text = text
        self.mapping = mapping


def _fake_redact_text(text, engine_config, current_mapping=None):
    mapping = dict(current_mapping or {})
    mapping[text] = mapping.get(text, len(mapping))
    replacement = engine_config.masking.replacement_text if engine_config.masking else "[X]"
    return _FakeResult(text.replace("SECRET", replacement), mapping)


def _install_engine(redaction_config_cls=_FakeRedactionConfig):
    module = types.ModuleType("ee_utils.redaction")
    module.MaskingConfig = _FakeMaskingConfig
    module.RedactionConfig = redaction_config_cls
    module.redact_text = _fake_redact_text
    package = types.ModuleType("ee_utils")
    package.redaction = module
    patch = mock.patch.dict(sys.modules, {"ee_utils": package, "ee_utils.redaction": module})
    patch.start()
    return patch


class TestOffByDefault(unittest.TestCase):
    def test_segments_pass_through_unchanged(self):
        segments = [Segment("has SECRET in it", "p. 1")]
        with mock.patch.object(config, "PII_REDACTION", False):
            result, mapping = redact_segments(segments)
        self.assertEqual(result, segments)
        self.assertIsNone(mapping)

    def test_an_empty_list_is_not_an_error(self):
        with mock.patch.object(config, "PII_REDACTION", True):
            result, mapping = redact_segments([])
        self.assertEqual(result, [])
        self.assertIsNone(mapping)


class TestTheEngineIsRequiredOnceSwitchedOn(unittest.TestCase):
    def test_a_missing_engine_stops_the_run_rather_than_sending_unredacted_text(self):
        segments = [Segment("has SECRET in it", "p. 1")]
        with mock.patch.object(config, "PII_REDACTION", True):
            with self.assertRaises(RedactionUnavailable) as raised:
                redact_segments(segments)
        self.assertIn("PII_REDACTION", str(raised.exception))

    def test_a_failed_call_into_the_engine_also_stops_the_run(self):
        patch = _install_engine()
        self.addCleanup(patch.stop)

        def broken(text, engine_config, current_mapping=None):
            raise RuntimeError("engine exploded")

        with mock.patch("ee_utils.redaction.redact_text", broken), \
             mock.patch.object(config, "PII_REDACTION", True):
            with self.assertRaises(RedactionUnavailable):
                redact_segments([Segment("text", "p. 1")])


class TestRedactionOnceSwitchedOn(unittest.TestCase):
    def setUp(self):
        self.patch = _install_engine()
        self.addCleanup(self.patch.stop)
        self.on = mock.patch.object(config, "PII_REDACTION", True)
        self.on.start()
        self.addCleanup(self.on.stop)

    def test_locators_survive_even_though_text_changes(self):
        segments = [Segment("contains SECRET data", "p. 1"), Segment("nothing here", "p. 2")]
        result, _ = redact_segments(segments)
        self.assertEqual([s.locator for s in result], ["p. 1", "p. 2"])
        self.assertIn("[REDACTED]", result[0].text)
        self.assertNotIn("SECRET", result[0].text)

    def test_the_configured_replacement_text_is_used(self):
        with mock.patch.object(config, "PII_REDACTION_REPLACEMENT_TEXT", "<<HIDDEN>>"):
            result, _ = redact_segments([Segment("has SECRET here", "p. 1")])
        self.assertIn("<<HIDDEN>>", result[0].text)

    def test_the_mapping_carries_across_segments_and_documents(self):
        """One name masked the same way everywhere in the pack, not once per passage."""
        first, mapping = redact_segments([Segment("SECRET one", "p. 1")])
        second, mapping = redact_segments([Segment("SECRET two", "p. 2")], mapping)
        self.assertEqual(len(mapping), 2)

    def test_a_setting_the_installed_config_does_not_accept_is_dropped_not_fatal(self):
        """The exact RedactionConfig shape was inferred from redact_file's signature, not
        confirmed -- an installed version narrower than guessed must not crash ingestion."""
        self.patch.stop()
        narrow = _install_engine(_NarrowRedactionConfig)
        self.addCleanup(narrow.stop)

        with mock.patch.object(config, "PII_REDACTION_SENSITIVITY", "strict"):
            with self.assertLogs("scenario_generator.ingest.redaction", level="WARNING") as logs:
                result, _ = redact_segments([Segment("has SECRET data", "p. 1")])
        self.assertIn("[REDACTED]", result[0].text)
        self.assertTrue(any("sensitivity" in line for line in logs.output))


class TestWiredIntoIngestion(unittest.TestCase):
    """build_corpus calls redaction after read_document and before the corpus is assembled --
    this pins that the wiring point is exactly there, using an injected stub rather than the
    real engine."""

    def test_every_readable_document_is_redacted_before_joining_the_corpus(self):
        import tempfile
        from pathlib import Path

        directory = Path(tempfile.mkdtemp())
        (directory / "a.md").write_text("first document", encoding="utf-8")
        (directory / "b.md").write_text("second document", encoding="utf-8")

        seen = []

        def stub_redact(segments, mapping=None):
            seen.append([s.text for s in segments])
            marked = [Segment(f"REDACTED::{s.text}", s.locator) for s in segments]
            return marked, mapping

        corpus = build_corpus([directory / "a.md", directory / "b.md"], redact=stub_redact)

        self.assertEqual(len(seen), 2)                        # called once per readable document
        self.assertIn("REDACTED::first document", corpus.text)
        self.assertIn("REDACTED::second document", corpus.text)

    def test_an_unreadable_document_is_never_sent_to_redaction(self):
        import tempfile
        from pathlib import Path

        directory = Path(tempfile.mkdtemp())
        (directory / "empty.md").write_text("   ", encoding="utf-8")

        calls = []
        build_corpus([directory / "empty.md"],
                     redact=lambda segments, mapping=None: (calls.append(1), (segments, mapping))[1])
        self.assertEqual(calls, [])

    def test_images_never_reach_redaction_either(self):
        import base64
        import tempfile
        from pathlib import Path

        path = Path(tempfile.mkdtemp()) / "flow.png"
        path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
            "IQAAAABJRU5ErkJggg=="))
        calls = []
        corpus = build_corpus([path],
                              redact=lambda segments, mapping=None: (calls.append(1), (segments, mapping))[1])
        self.assertEqual(calls, [])
        self.assertEqual(corpus.diagrams, [path])


if __name__ == "__main__":
    unittest.main()
