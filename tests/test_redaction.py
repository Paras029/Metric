"""Redaction: off by default, a hard stop rather than a silent skip once it is on.

The engine itself -- ee_utils.redaction -- is an internal package not present in this repository
or its test environment, so it is stubbed here the same way SafeChain is stubbed in
test_gateway.py: a real module installed on sys.modules for the duration of a test, standing in
for the real contract (MaskingConfig, RedactionConfig, redact_text) rather than a loose mock.
"""
import io
import sys
import types
import unittest
from unittest import mock

from scenario_generator.ingest.extraction import build_corpus
from scenario_generator.ingest.readers import Segment
from scenario_generator.ingest.redaction import RedactionUnavailable, redact_segments
from scenario_generator.llm import config
from scenario_generator.webapp.app import _run_documents, create_app
from scenario_generator.webapp.workspace import Workspace


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

    def test_should_redact_forces_one_file_independent_of_the_global_setting(self):
        """The per-file toggle in the interface must reach build_corpus as force=True for that
        file only, regardless of whether PII_REDACTION is on globally."""
        import tempfile
        from pathlib import Path

        directory = Path(tempfile.mkdtemp())
        marked = directory / "sensitive.md"
        unmarked = directory / "ordinary.md"
        marked.write_text("marked document", encoding="utf-8")
        unmarked.write_text("ordinary document", encoding="utf-8")

        seen_force = {}

        def stub_redact(segments, mapping=None, force=False):
            seen_force[segments[0].text] = force
            return segments, mapping

        with mock.patch.object(config, "PII_REDACTION", False):
            build_corpus([marked, unmarked], redact=stub_redact,
                        should_redact=lambda path: path.name == "sensitive.md")

        self.assertTrue(seen_force["marked document"])
        self.assertFalse(seen_force["ordinary document"])

    def test_every_readable_document_is_redacted_before_joining_the_corpus(self):
        import tempfile
        from pathlib import Path

        directory = Path(tempfile.mkdtemp())
        (directory / "a.md").write_text("first document", encoding="utf-8")
        (directory / "b.md").write_text("second document", encoding="utf-8")

        seen = []

        def stub_redact(segments, mapping=None, force=False):
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
                     redact=lambda segments, mapping=None, force=False:
                         (calls.append(1), (segments, mapping))[1])
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
                              redact=lambda segments, mapping=None, force=False:
                                  (calls.append(1), (segments, mapping))[1])
        self.assertEqual(calls, [])
        self.assertEqual(corpus.diagrams, [path])


class _StubSummary(dict):
    """The counts _run_documents reads off a real IngestResult.summary."""

    def __init__(self):
        super().__init__(documents=1, readable=1, drawn_on=0, answered=0, usable=0,
                         rejected=0, to_ask=0, set_aside=0)


class _StubIngestResult:
    def __init__(self):
        self.evidence_path = "workspace_evidence.json"
        self.context_path = "workspace_context.md"
        self.summary = _StubSummary()


class TestThePerFileToggleReachesIngestion(unittest.TestCase):
    """The interface's per-file checkbox has to survive two hops -- workspace state, then a
    should_redact callable built from it -- before it reaches build_corpus. This pins the
    middle hop, in _run_documents, without needing a full model-backed ingestion run."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.workspace = Workspace.create(Path(tempfile.mkdtemp()), "Redact toggle")
        sources = self.workspace.root / "sources" / "model_doc"
        sources.mkdir(parents=True)
        (sources / "spec.md").write_text("has content", encoding="utf-8")
        (sources / "other.md").write_text("has content too", encoding="utf-8")

    def test_only_the_marked_file_is_forced(self):
        self.workspace.set_redact("model_doc", "spec.md", True)

        captured = {}

        def fake_ingest_documents(paths, prefix, progress=None, cancel=None, should_redact=None):
            captured["should_redact"] = should_redact
            return _StubIngestResult()

        with mock.patch("scenario_generator.webapp.app.ingest_documents", fake_ingest_documents):
            _run_documents(self.workspace)

        should_redact = captured["should_redact"]
        marked = self.workspace.root / "sources" / "model_doc" / "spec.md"
        unmarked = self.workspace.root / "sources" / "model_doc" / "other.md"
        self.assertTrue(should_redact(marked))
        self.assertFalse(should_redact(unmarked))


class TestTheToggleRouteThroughTheInterface(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.root = Path(tempfile.mkdtemp())
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.client.post("/workspaces", data={"name": "Redact toggle"})

        self.client.post("/stage/documents/upload",
                         data={"files": (io.BytesIO(b"# Spec\nSome content."), "spec.md"),
                              "group": "model_doc"},
                         content_type="multipart/form-data")

    def _workspace(self) -> Workspace:
        directories = [p for p in self.root.iterdir() if (p / "workspace.json").exists()]
        self.assertEqual(len(directories), 1)
        return Workspace.load(directories[0])

    def test_checking_the_box_marks_the_file(self):
        response = self.client.post("/stage/documents/redact",
                                    data={"group": "model_doc", "name": "spec.md", "on": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self._workspace().is_marked_for_redaction("model_doc", "spec.md"))

    def test_unchecking_it_clears_the_mark(self):
        self.client.post("/stage/documents/redact",
                         data={"group": "model_doc", "name": "spec.md", "on": "1"})
        # An unchecked checkbox submits no "on" field at all -- this is the real request shape.
        self.client.post("/stage/documents/redact", data={"group": "model_doc", "name": "spec.md"})
        self.assertFalse(self._workspace().is_marked_for_redaction("model_doc", "spec.md"))

    def test_the_toggle_is_refused_for_a_group_redaction_cannot_apply_to(self):
        """Diagrams have no text to redact, and an unknown group name is not a real upload."""
        self.client.post("/stage/documents/redact",
                         data={"group": "diagrams", "name": "flow.png", "on": "1"})
        self.assertFalse(self._workspace().is_marked_for_redaction("diagrams", "flow.png"))

    def test_removing_the_file_also_clears_its_mark(self):
        self.client.post("/stage/documents/redact",
                         data={"group": "model_doc", "name": "spec.md", "on": "1"})
        self.client.post("/stage/documents/remove", data={"group": "model_doc", "name": "spec.md"})
        self.assertFalse(self._workspace().is_marked_for_redaction("model_doc", "spec.md"))

    def test_the_checkbox_state_is_reflected_back_on_the_page(self):
        self.client.post("/stage/documents/redact",
                         data={"group": "model_doc", "name": "spec.md", "on": "1"})
        page = self.client.get("/stage/documents").data.decode()
        # The checkbox for spec.md must be checked; a loose "checked" anywhere on the page would
        # pass even if it landed on the wrong element, so this looks at the specific input.
        self.assertRegex(page, r'name="name" value="spec\.md">\s*<label[^>]*>\s*<input '
                                r'type="checkbox" name="on" value="1"\s+checked')


if __name__ == "__main__":
    unittest.main()
