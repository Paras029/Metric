"""What a stage tells the person watching it, while it runs and after it finishes.

None of this changes what the pipeline produces, which is exactly why it is worth pinning: a
figure that is wrong, a bar that does not move, or a label only its author can read all survive
every other test in this suite while making the tool harder to use than it needs to be.

Three properties, each of which has a plausible-looking wrong version:

*Documents and images are counted apart.* A workflow drawn across five pictures is one flow
submitted as five files. Rolled into one number it reports a pack six times the size it was.

*A batched pass reports replies as they land.* Every chunk goes out at once, so a pass that
reports only what it has finished applying is silent for its whole duration and then jumps to
finished. What it applies is a fraction of a second at the end of a wait of minutes.

*Progress counts what has happened, not what has been started.* Three reading calls dispatched
together and counted on dispatch put the bar a quarter along in the first second and leave it
there for the length of the longest call.
"""
import tempfile
import unittest
from pathlib import Path

from scenario_generator.core.evidence import DocumentRef, EvidenceRecord, summarise
from scenario_generator.core.models import Persona, Scenario
from scenario_generator.llm.calling import call_batch


def _scenario(scenario_id: str, persona: Persona) -> Scenario:
    """One scenario, minimal but complete enough for the writer to be handed it."""
    return Scenario(id=scenario_id, path=[], category="Happy path", persona=persona,
                    seeded_state="S-00", termination="S-01", capabilities=[], tools=[],
                    touches_state_change=False)


class TestPicturesAreNotDocuments(unittest.TestCase):
    def _record(self):
        return EvidenceRecord(documents=[
            DocumentRef("model_document.pdf", "pdf", 61, drawn_on=True),
            DocumentRef("vendor.docx", "docx", 12),
            DocumentRef("flow_1.png", "diagram", 1, is_image=True, drawn_on=True),
            DocumentRef("flow_2.png", "diagram", 1, is_image=True, drawn_on=True),
            DocumentRef("flow_3.png", "unreadable", note="unreadable", is_image=True),
        ])

    def test_a_workflow_split_over_images_is_not_reported_as_that_many_documents(self):
        counts = summarise(self._record())
        self.assertEqual(counts["texts"], 2)
        self.assertEqual(counts["images"], 3)
        # The old rolled-up number, still available for anything that wants the total.
        self.assertEqual(counts["documents"], 5)

    def test_an_image_that_could_not_be_read_is_still_counted_as_an_image(self):
        """Otherwise a failed diagram silently moves from the image tally to the document tally,
        and the two lines disagree about how many files were submitted."""
        counts = summarise(self._record())
        self.assertEqual(counts["images"], 3)
        self.assertEqual(counts["images_read"], 2)
        self.assertEqual(counts["texts"], 2)

    def test_the_reading_reports_documents_and_images_separately(self):
        from scenario_generator.webapp.runners import _read_documents
        from scenario_generator.webapp.workspace import Workspace

        record = self._record()

        class _Result:
            evidence_path, context_path = "e.json", "c.md"
            summary = summarise(record)

        workspace = Workspace.create(Path(tempfile.mkdtemp()), "Counting")
        sources = workspace.root / "sources" / "model_doc"
        sources.mkdir(parents=True)
        (sources / "spec.md").write_text("content", encoding="utf-8")

        from unittest import mock
        with mock.patch("scenario_generator.webapp.runners.ingest_documents",
                        lambda *a, **k: _Result()):
            summary = _read_documents(workspace)

        self.assertEqual(summary["Documents read"], "2 of 2")
        self.assertEqual(summary["Workflow images read"], "2 of 3")

    def test_a_pack_with_no_images_does_not_show_an_image_line(self):
        """A line reading "0 of 0" is a question about why it is there."""
        from scenario_generator.webapp.runners import _read_documents
        from scenario_generator.webapp.workspace import Workspace
        from unittest import mock

        class _Result:
            evidence_path, context_path = "e.json", "c.md"
            summary = summarise(EvidenceRecord(documents=[DocumentRef("only.pdf", "pdf", 4)]))

        workspace = Workspace.create(Path(tempfile.mkdtemp()), "No images")
        sources = workspace.root / "sources" / "model_doc"
        sources.mkdir(parents=True)
        (sources / "only.pdf").write_bytes(b"x")

        with mock.patch("scenario_generator.webapp.runners.ingest_documents",
                        lambda *a, **k: _Result()):
            summary = _read_documents(workspace)

        self.assertIn("Documents read", summary)
        self.assertNotIn("Workflow images read", summary)


class TestABatchedPassReportsAsRepliesLand(unittest.TestCase):
    def test_a_batching_completion_is_told_how_to_report(self):
        seen = []

        def complete(system, user, **kwargs):
            return "{}"

        def batch(system, messages, on_progress=None, **kwargs):
            for index in range(len(messages)):
                if on_progress:
                    on_progress(index + 1, len(messages))
            return ["{}"] * len(messages)

        complete.batch = batch
        call_batch(complete, "S", ["a", "b", "c"], on_progress=lambda done, total: seen.append(done))
        self.assertEqual(seen, [1, 2, 3, 3])                 # once per wave, then the final total

    def test_a_batching_completion_that_ignores_the_callback_still_reports_once(self):
        """A batch function taking ``**kwargs`` accepts every argument name and may honour none
        of them. A pass that reported nothing at all is the outcome worth ruling out."""
        seen = []

        def complete(system, user, **kwargs):
            return "{}"

        def batch(system, messages, **kwargs):               # swallows on_progress
            return ["{}"] * len(messages)

        complete.batch = batch
        call_batch(complete, "S", ["a", "b"], on_progress=lambda done, total: seen.append(done))
        self.assertEqual(seen, [2])

    def test_a_completion_with_no_batching_reports_per_message(self):
        seen = []
        call_batch(lambda system, user: "{}", "S", ["a", "b", "c"],
                   on_progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_the_writer_reports_before_it_has_applied_anything(self):
        """The property that matters: the first report arrives while replies are still coming
        back, not after the pass has finished with them."""
        from scenario_generator.core.models import IntakeData
        from scenario_generator.llm import ScenarioWriter

        reported = []

        def complete(system, user, **kwargs):
            return "{}"

        def batch(system, messages, on_progress=None, **kwargs):
            for index in range(len(messages)):
                if on_progress:
                    on_progress(index + 1, len(messages))
            # Nothing usable comes back, so the applying loop below reports nothing of its own --
            # every report this test sees came from a reply landing.
            return ["{}"] * len(messages)

        complete.batch = batch
        intake = IntakeData(use_case={"Use case name": "X"},
                            personas=[Persona("P1", "Default", [], True)],
                            capabilities=[], decisions=[], states=[], tools=[])
        scenarios = [_scenario(f"SC-{n:03d}", intake.personas[0]) for n in range(1, 10)]

        ScenarioWriter(complete=complete, batch_size=3,
                       progress=lambda message, done=0, total=0: reported.append(done)).write(
            scenarios, intake)

        self.assertEqual(reported[:3], [3, 6, 9])


class TestIngestionCountsWhatHasHappened(unittest.TestCase):
    def test_the_bar_does_not_advance_on_starting_a_call(self):
        """Three reading calls go out together. Counting them as sent puts the bar a quarter
        along before a single reply exists."""
        import json

        from scenario_generator.ingest import extract_documents

        directory = Path(tempfile.mkdtemp())
        (directory / "notes.md").write_text(
            "# Flow\n\nThe agent confirms identity, then looks up the account.\n", encoding="utf-8")

        seen = []

        def complete(system, user, **kwargs):
            # Every call records how far along the bar claims to be at the moment it is made.
            seen.append(max((done for _, done, _ in progress_calls), default=0))
            return json.dumps({})


        progress_calls = []

        def progress(message, done=0, total=0):
            progress_calls.append((message, done, total))

        extract_documents([directory / "notes.md"], complete=complete, progress=progress)

        # One file parsed before any call is made, and nothing beyond that until a reply lands.
        self.assertEqual(seen[0], 1, "a call was counted before it had returned")

    def test_parsing_is_part_of_the_total_rather_than_dead_time(self):
        import json

        from scenario_generator.ingest import extract_documents

        directory = Path(tempfile.mkdtemp())
        for name in ("one.md", "two.md", "three.md"):
            (directory / name).write_text("# Flow\n\nThe agent confirms identity.\n",
                                          encoding="utf-8")

        progress_calls = []
        extract_documents([directory / n for n in ("one.md", "two.md", "three.md")],
                          complete=lambda system, user, **kwargs: json.dumps({}),
                          progress=lambda message, done=0, total=0:
                              progress_calls.append((message, done, total)))

        parsing = [entry for entry in progress_calls if entry[0] == "Opening the submitted files"]
        self.assertEqual(len(parsing), 3)
        # Each parsed file advances the bar, against a total that already knows about the calls.
        self.assertEqual([done for _, done, _ in parsing], [1, 2, 3])
        self.assertTrue(all(total > 3 for _, _, total in parsing))

    def test_the_bar_never_reports_more_than_finished(self):
        import json

        from scenario_generator.ingest import extract_documents

        directory = Path(tempfile.mkdtemp())
        (directory / "notes.md").write_text("# Flow\n\nThe agent confirms identity.\n",
                                            encoding="utf-8")

        progress_calls = []
        extract_documents([directory / "notes.md"],
                          complete=lambda system, user, **kwargs: json.dumps({}),
                          progress=lambda message, done=0, total=0:
                              progress_calls.append((done, total)))

        self.assertTrue(all(done <= total for done, total in progress_calls),
                        f"progress went past its own total: {progress_calls}")
        # And never goes backwards, which a bar that jumps around is usually doing.
        counts = [done for done, _ in progress_calls]
        self.assertEqual(counts, sorted(counts))


class TestTimestampsAreReadable(unittest.TestCase):
    """Stored as UTC in ISO form, which is right for a file and wrong for a page."""

    def setUp(self):
        from scenario_generator.webapp.app import create_app
        self.app = create_app(Path(tempfile.mkdtemp()))
        self.when = self.app.jinja_env.filters["when"]

    def test_an_iso_timestamp_becomes_something_a_person_reads(self):
        self.assertEqual(self.when("2026-08-08T14:23:11+00:00"), "08 Aug 2026 at 14:23 UTC")

    def test_a_timestamp_with_no_zone_is_read_as_utc_rather_than_as_local(self):
        """Every timestamp this tool writes is UTC. Reading a bare one as machine-local would
        shift it by the offset of whichever machine happens to render it."""
        self.assertEqual(self.when("2026-08-08T14:23:11"), "08 Aug 2026 at 14:23 UTC")

    def test_something_that_is_not_a_timestamp_is_passed_through_rather_than_raising(self):
        self.assertEqual(self.when("not a date"), "not a date")
        self.assertEqual(self.when(""), "")
        self.assertEqual(self.when(None), "")

    def test_the_page_carries_the_machine_readable_form_alongside_the_readable_one(self):
        """The browser re-renders these in the viewer's own timezone, which it can only do from
        the original instant -- so the ISO value has to survive into the markup."""
        from scenario_generator.webapp.workspace import Workspace

        root = Path(self.app.config["WORKSPACE_ROOT"])
        workspace = Workspace.create(root, "Timestamps")
        # A stage that still shows a Result block. The intake stage's was removed: what that
        # stage produces is the declaration, and the declaration is shown in full underneath it.
        workspace.complete("intake")
        workspace.complete("workflow", summary={"Routes walked": 12})
        stamp = workspace.state("workflow").updated_at

        with self.app.test_client() as client:
            with client.session_transaction() as session:
                session["workspace"] = workspace.root.name
            page = client.get("/stage/workflow").get_data(as_text=True)

        self.assertIn(f'datetime="{stamp}"', page)
        self.assertIn('class="when"', page)


class TestAQuestionSaysWhatAnAnswerLooksLike(unittest.TestCase):
    def test_every_gap_carries_an_example_of_its_own_answer(self):
        """A question can be clear about what it asks and still leave the shape of the reply
        unsaid -- a word, a list, or a sentence. The example settles that where it is felt."""
        from scenario_generator.core.gaps import find_gaps
        from scenario_generator.core.models import Capability, Decision, IntakeData, Persona, State

        intake = IntakeData(
            use_case={},
            personas=[Persona("P1", "Default", [], True)],
            capabilities=[Capability("CAP-01", "Authentication", "")],
            decisions=[Decision("DEC-01", "Identity check", "", "", ["Pass"])],
            states=[State("S-00", "Start", "", ["DEC-01"], False),
                    State("S-01", "DEC-01=Pass", "", [], True, "")],
            tools=[])

        gaps = find_gaps(intake)
        self.assertTrue(gaps)
        without = [gap.question for gap in gaps if not gap.example]
        self.assertEqual(without, [], "these gaps do not say what an answer looks like")


if __name__ == "__main__":
    unittest.main()
