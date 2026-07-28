"""Two things that only go wrong once the tool is actually being used.

The workspace record is written by a stage running on a background thread and read by the page
polling for its progress, several times a minute, for as long as a run takes. The stage runners
are handed a completion function that may be the real gateway or a stub, and the two do not have
the same signature.

Both were previously handled in a way that worked until it didn't: an in-place write that a
reader could catch mid-truncation, and a ``try/except TypeError`` that could not tell a signature
mismatch from a genuine error inside the call.
"""
import threading
import time
import unittest
from pathlib import Path
import tempfile

from scenario_generator.llm import config
from scenario_generator.llm.calling import accepts, call
from scenario_generator.webapp.workspace import Workspace


class TestTheRecordIsNeverCaughtHalfWritten(unittest.TestCase):
    def test_a_reader_always_sees_a_complete_record(self):
        """A truncated read shows the workspace as missing, and the page 404s mid-run."""
        workspace = Workspace.create(Path(tempfile.mkdtemp()), "Race")
        stop = threading.Event()
        failures = []

        def write():
            count = 0
            while not stop.is_set():
                workspace.report_progress("documents", f"step {count}", count, 100)
                count += 1

        def read():
            while not stop.is_set():
                try:
                    Workspace.load(workspace.root)
                except Exception as exc:                   # any read failure is the bug
                    failures.append(exc)
                    return

        writer, reader = threading.Thread(target=write), threading.Thread(target=read)
        writer.start()
        reader.start()
        time.sleep(1.0)
        stop.set()
        writer.join()
        reader.join()

        self.assertEqual(failures, [], f"a concurrent read failed: {failures[:1]}")

    def test_no_temporary_files_are_left_behind(self):
        workspace = Workspace.create(Path(tempfile.mkdtemp()), "Tidy")
        workspace.save()
        self.assertEqual(list(workspace.root.glob("*.tmp")), [])


class TestCallingACompletionFunction(unittest.TestCase):
    def test_a_stub_that_takes_two_arguments_is_called_with_two(self):
        def stub(system, user):
            return f"{system}|{user}"

        self.assertEqual(call(stub, "S", "U", tier=config.FAST), "S|U")

    def test_a_gateway_that_takes_a_tier_is_given_one(self):
        seen = {}

        def gateway(system, user, tier=None):
            seen["tier"] = tier
            return ""

        call(gateway, "S", "U", tier=config.JUDGEMENT)
        self.assertIs(seen["tier"], config.JUDGEMENT)

    def test_a_stub_taking_keyword_arguments_is_given_the_tier(self):
        seen = {}

        def stub(system, user, **kwargs):
            seen.update(kwargs)
            return ""

        call(stub, "S", "U", tier=config.STANDARD)
        self.assertIs(seen["tier"], config.STANDARD)

    def test_a_real_type_error_inside_the_call_is_raised_as_itself(self):
        """The failure the old try/except swallowed: it retried and reported the wrong error."""
        def gateway(system, user, tier=None):
            raise TypeError("cannot serialise the payload")

        with self.assertRaises(TypeError) as raised:
            call(gateway, "S", "U", tier=config.FAST)
        self.assertIn("serialise", str(raised.exception))

    def test_extra_arguments_are_passed_only_where_they_fit(self):
        def with_images(system, user, images):
            return f"{len(images)}"

        self.assertEqual(call(with_images, "S", "U", tier=config.JUDGEMENT, images=[1, 2]), "2")

    def test_a_callable_with_no_readable_signature_still_works(self):
        self.assertTrue(accepts(lambda s, u, **k: "", "tier"))
        self.assertFalse(accepts(len, "tier"))


if __name__ == "__main__":
    unittest.main()
