"""Stopping a running stage.

A stop has to do two things or it is not worth having: it must actually prevent further model
calls from being sent, and it must leave the workspace exactly as it was before the run started,
so the stage comes back ready rather than stuck looking failed. This drives that end to end
through the interface, the same way a person clicking the button would.
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from metric.domain.intake import write_template
from metric.llm import cancellation
from metric.llm.calling import call_batch
from metric.web.server import create_app
from metric.web.workspace import Workspace


def _intake_workbook(directory: Path) -> Path:
    path = directory / "intake.xlsx"
    write_template(str(path))

    from openpyxl import load_workbook
    book = load_workbook(path)
    book["L1 Use Case"]["B1"] = "Test agent"
    book["L1 Use Case"]["B2"] = "Answer questions"
    book["Personas"].append(["P1", "Cooperative user", "Happy path", "Y"])
    book["L2 Capabilities"].append(["CAP-01", "Authentication", "Gating"])
    book["L3 Decisions"].append(
        ["DEC-01", "Identity check", "CAP-01", "credentials", "Pass / Fail", "User", 2, ""])
    book["L4 States"].append(["S-00", "Start", "Session begins", "DEC-01", "No", ""])
    book["L4 States"].append(["S-01", "DEC-01=Pass", "Authenticated", "", "Yes", "Happy path"])
    book["L4 States"].append(["S-02", "DEC-01=Fail", "Locked out", "", "Yes", "Termination"])
    book["Tools"].append(["Identity service", "CAP-01", "No"])
    book.save(path)
    return path


class TestCancellationPrimitives(unittest.TestCase):
    def test_nothing_to_check_never_stops_anything(self):
        cancellation.check(None)                            # must not raise

    def test_an_unset_event_is_not_a_stop(self):
        event = threading.Event()
        cancellation.check(event)                            # must not raise
        self.assertFalse(cancellation.is_set(event))

    def test_a_set_event_raises_stopped(self):
        event = threading.Event()
        event.set()
        self.assertTrue(cancellation.is_set(event))
        with self.assertRaises(cancellation.Stopped):
            cancellation.check(event)


class TestCallBatchHonoursCancel(unittest.TestCase):
    """The plain-function fallback path -- no ``.batch`` attribute -- checked directly, since
    that is what a stub completion function still looks like."""

    def test_nothing_is_sent_once_cancel_is_already_set(self):
        event = threading.Event()
        event.set()
        calls = []

        def complete(system, user, **kwargs):
            calls.append(user)
            return "ok"

        results = call_batch(complete, "s", ["one", "two", "three"], cancel=event)

        self.assertEqual(calls, [])
        self.assertTrue(all(isinstance(r, cancellation.Stopped) for r in results))

    def test_cancelling_partway_stops_the_rest(self):
        event = threading.Event()
        calls = []

        def complete(system, user, **kwargs):
            calls.append(user)
            if user == "two":
                event.set()                                 # as if a stop landed mid-batch
            return "ok"

        results = call_batch(complete, "s", ["one", "two", "three"], cancel=event)

        self.assertEqual(calls, ["one", "two"])              # "three" was never sent
        self.assertEqual(results[0], "ok")
        self.assertEqual(results[1], "ok")
        self.assertIsInstance(results[2], cancellation.Stopped)


class TestStoppingAStageThroughTheInterface(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.client.post("/workspaces", data={"name": "Stoppable"})
        self.slug = "stoppable"

        scratch = Path(tempfile.mkdtemp())
        intake_path = _intake_workbook(scratch)
        with open(intake_path, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, intake_path.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")

        self.client.post("/stage/intake/run")
        self._settle("intake")
        self.client.post("/stage/workflow/run")
        self._settle("workflow")

    # Generous on purpose. Every wait here is a poll that ends the moment the thing it is waiting
    # for happens, so a long ceiling costs nothing when the machine is quick -- and the whole
    # suite runs these stages alongside CPU-bound work, where a tight ceiling turns a slow moment
    # into a failure that says nothing about stopping.
    def _settle(self, key: str, timeout: float = 60.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.client.get(f"/stage/{key}/progress").get_json()["status"]
            if status != "running":
                return status
            time.sleep(0.02)
        self.fail(f"{key} was still running after {timeout}s")

    def test_stopping_halts_further_calls_and_leaves_the_stage_ready_again(self):
        calls = []
        release = threading.Event()

        def blocking_complete(system, user, **kwargs):
            # Stands in for a model call in flight when the stop is clicked: it is allowed to
            # finish rather than being torn down, but nothing after it may be sent. The wait is
            # long because it must not expire on its own -- a call that returned early because a
            # loaded machine took its timeout would let the next one be sent before the stop
            # landed, and the test would be measuring the timeout rather than the stop.
            calls.append(user)
            release.wait(timeout=60)
            return "{}"

        with mock.patch("metric.phases.scenario_generator.materiality.assess.ask_llm", blocking_complete):
            self.client.post("/stage/materiality/run")

            deadline = time.time() + 60
            while not calls and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(calls, "the materiality call never started")

            stopped = self.client.post("/stage/materiality/stop")
            self.assertEqual(stopped.status_code, 302)
            in_flight = len(calls)
            release.set()                                   # let the in-flight call finish

            status = self._settle("materiality")

        self.assertEqual(status, "stopped")
        # The property is that nothing further was sent, not that exactly one call had been:
        # how many were already in flight when the button was pressed is a matter of timing.
        self.assertEqual(len(calls), in_flight,
                         "no call may be sent after a stop is requested")
        page = self.client.get("/stage/materiality").data.decode()
        self.assertIn("Stopped", page)

        # And it comes back ready to run rather than stuck: a second, uninterrupted run
        # completes normally.
        with mock.patch("metric.phases.scenario_generator.materiality.assess.ask_llm", lambda s, u, **k: "{}"):
            self.client.post("/stage/materiality/run")
            self.assertEqual(self._settle("materiality"), "complete")

    def test_a_finished_run_never_overwrites_the_status_of_a_newer_one(self):
        """The failure the test above kept tripping over, reproduced directly.

        A stage's thread writes its verdict as it unwinds, and that is not always faster than the
        person watching: stop a long run, see it stop, press Run again, and the old thread's
        "stopped" lands on a stage that has already started working. The new run then reads as
        stopped -- or, if the old run failed, as failed with the previous run's error.
        """
        workspace = Workspace.load(self.root / self.slug)
        first = workspace.mark_running("materiality")

        # The person presses Run again before the first thread has finished unwinding.
        restarted = Workspace.load(self.root / self.slug)
        second = restarted.mark_running("materiality")
        self.assertNotEqual(first, second)

        # Now the first run reports. Both verdicts, because both are written the same way.
        Workspace.load(self.root / self.slug).mark_stopped("materiality", run_id=first)
        self.assertEqual(Workspace.load(self.root / self.slug).state("materiality").status,
                         "running")
        Workspace.load(self.root / self.slug).mark_failed("materiality", "old error",
                                                          run_id=first)
        state = Workspace.load(self.root / self.slug).state("materiality")
        self.assertEqual(state.status, "running")
        self.assertNotIn("old error", state.note)

        # The run that owns the stage still settles it.
        Workspace.load(self.root / self.slug).complete("materiality", summary={"Scenarios": 1},
                                                       run_id=second)
        self.assertEqual(Workspace.load(self.root / self.slug).state("materiality").status,
                         "complete")

    def test_a_finished_run_does_not_unmark_a_newer_one_as_alive(self):
        """The other half of the same race, and the one that survived the first fix.

        A run ending clears the marker that says its stage is alive in this process. Clearing it
        by stage alone removed the marker a newer run had just installed -- and the reconciler,
        which exists to catch a stage left running by an interrupted process, then read a stage
        that was genuinely working as one whose thread had died.
        """
        workspace = Workspace.load(self.root / self.slug)
        first = workspace.mark_running("materiality")
        stale = Workspace.load(self.root / self.slug)      # the old thread, before it unwinds

        second = Workspace.load(self.root / self.slug).mark_running("materiality")
        stale.mark_stopped("materiality", run_id=first)

        state = Workspace.load(self.root / self.slug).state("materiality")
        self.assertEqual(state.status, "running")
        self.assertEqual(state.run_id, second)

    def test_a_verdict_with_no_run_id_is_still_honoured(self):
        """Nothing that used to write a status has to be changed for it to keep working."""
        workspace = Workspace.load(self.root / self.slug)
        workspace.mark_running("materiality")
        Workspace.load(self.root / self.slug).mark_stopped("materiality")
        self.assertEqual(Workspace.load(self.root / self.slug).state("materiality").status,
                         "stopped")

    def test_stopping_a_stage_nobody_is_listening_to_is_a_quiet_no_op(self):
        """Nothing is running, so there is nothing to stop -- and nothing should break either."""
        response = self.client.post("/stage/materiality/stop")
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
