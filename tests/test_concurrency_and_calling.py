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
from unittest import mock
import tempfile

from scenario_generator.llm import config
from scenario_generator.llm.calling import accepts, call, call_batch
from scenario_generator.webapp import workspace as workspace_module
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

    def test_writers_through_different_instances_do_not_collide(self):
        """The real crash: ingestion reports progress from three threads at once, and a request
        handling a click constructs its own separate Workspace pointed at the same file. An
        instance-level lock would not see the second writer; this is why the lock is keyed on the
        path instead."""
        root = Workspace.create(Path(tempfile.mkdtemp()), "Race").root
        stop = threading.Event()
        failures = []

        def write(tag):
            count = 0
            while not stop.is_set():
                try:
                    Workspace.load(root).report_progress("documents", f"{tag}-{count}", count, 100)
                except Exception as exc:
                    failures.append(exc)
                    return
                count += 1

        writers = [threading.Thread(target=write, args=(tag,)) for tag in "abcde"]
        for w in writers:
            w.start()
        time.sleep(1.0)
        stop.set()
        for w in writers:
            w.join()

        self.assertEqual(failures, [], f"a concurrent write failed: {failures[:1]}")
        Workspace.load(root)                                # the file is left valid JSON


class TestSurvivingAWindowsFileLock(unittest.TestCase):
    """OneDrive, antivirus, or another process can hold a file open for a moment on Windows, and
    os.replace reports that as PermissionError -- nothing to do with actual permissions. The fix
    is a short retry, since the hold clears on its own; these pin that it retries rather than
    failing immediately, and that it still gives up if the lock genuinely never clears."""

    def test_a_transient_lock_is_retried_rather_than_raised(self):
        workspace = Workspace.create(Path(tempfile.mkdtemp()), "Locked")
        calls = {"count": 0}
        real_replace = workspace_module.os.replace

        def flaky(source, destination):
            calls["count"] += 1
            if calls["count"] < 3:
                raise PermissionError("[WinError 5] Access is denied")
            return real_replace(source, destination)

        with mock.patch.object(workspace_module.os, "replace", side_effect=flaky):
            with mock.patch.object(workspace_module.time, "sleep"):     # do not slow the test
                workspace.save()

        self.assertEqual(calls["count"], 3)
        self.assertEqual(Workspace.load(workspace.root).name, "Locked")

    def test_a_lock_that_never_clears_still_raises(self):
        """Retrying forever would hang the request; a permanent lock has to surface as a failure."""
        workspace = Workspace.create(Path(tempfile.mkdtemp()), "Stuck")

        with mock.patch.object(workspace_module.os, "replace",
                               side_effect=PermissionError("still denied")):
            with mock.patch.object(workspace_module.time, "sleep"):
                with self.assertRaises(PermissionError):
                    workspace.save()

    def test_the_temporary_file_is_cleaned_up_even_when_the_lock_never_clears(self):
        workspace = Workspace.create(Path(tempfile.mkdtemp()), "Stuck")

        with mock.patch.object(workspace_module.os, "replace",
                               side_effect=PermissionError("still denied")):
            with mock.patch.object(workspace_module.time, "sleep"):
                with self.assertRaises(PermissionError):
                    workspace.save()

        self.assertEqual(list(workspace.root.glob("*.tmp")), [])


class TestCallingACompletionFunction(unittest.TestCase):
    def test_a_stub_that_takes_two_arguments_is_called_with_two(self):
        def stub(system, user):
            return f"{system}|{user}"

        self.assertEqual(call(stub, "S", "U", tier=config.STANDARD), "S|U")

    def test_a_gateway_that_takes_a_tier_is_given_one(self):
        seen = {}

        def gateway(system, user, tier=None):
            seen["tier"] = tier
            return ""

        call(gateway, "S", "U", tier=config.JUDGEMENT)
        self.assertEqual(seen["tier"], config.JUDGEMENT)

    def test_a_stub_taking_keyword_arguments_is_given_the_tier(self):
        seen = {}

        def stub(system, user, **kwargs):
            seen.update(kwargs)
            return ""

        call(stub, "S", "U", tier=config.STANDARD)
        self.assertEqual(seen["tier"], config.STANDARD)

    def test_a_real_type_error_inside_the_call_is_raised_as_itself(self):
        """The failure the old try/except swallowed: it retried and reported the wrong error."""
        def gateway(system, user, tier=None):
            raise TypeError("cannot serialise the payload")

        with self.assertRaises(TypeError) as raised:
            call(gateway, "S", "U", tier=config.STANDARD)
        self.assertIn("serialise", str(raised.exception))

    def test_extra_arguments_are_passed_only_where_they_fit(self):
        def with_images(system, user, images):
            return f"{len(images)}"

        self.assertEqual(call(with_images, "S", "U", tier=config.JUDGEMENT, images=[1, 2]), "2")

    def test_a_callable_with_no_readable_signature_still_works(self):
        self.assertTrue(accepts(lambda s, u, **k: "", "tier"))
        self.assertFalse(accepts(len, "tier"))


class TestCallingInBatches(unittest.TestCase):
    """A pass written before batching existed only ever gave call_batch a plain function, and
    that has to keep working exactly as it did -- one call per message, through the same
    tier-aware call() as always. Real concurrency is opt-in, via a .batch attribute the
    production gateway sets and a test stub does not have to know about."""

    def test_a_plain_stub_is_called_once_per_message_and_returns_them_in_order(self):
        def stub(system, user, **kwargs):
            return user.upper()

        self.assertEqual(call_batch(stub, "S", ["a", "b", "c"]), ["A", "B", "C"])

    def test_the_tier_reaches_a_plain_stub_the_same_way_a_single_call_does(self):
        seen = []

        def stub(system, user, tier=None):
            seen.append(tier)
            return user

        call_batch(stub, "S", ["a", "b"], tier=config.STANDARD)
        self.assertEqual(seen, [config.STANDARD, config.STANDARD])

    def test_one_message_failing_does_not_stop_the_others(self):
        """Matches ask_llm_batch's own contract: a batch never fails as a whole."""
        def stub(system, user, **kwargs):
            if user == "bad":
                raise RuntimeError("boom")
            return user

        results = call_batch(stub, "S", ["good-1", "bad", "good-2"])
        self.assertEqual(results[0], "good-1")
        self.assertIsInstance(results[1], RuntimeError)
        self.assertEqual(results[2], "good-2")

    def test_a_completion_function_advertising_batch_is_used_instead(self):
        calls = []

        def stub(system, user, **kwargs):
            raise AssertionError("should not be called one at a time when .batch exists")

        def real_batch(system, user_messages, **kwargs):
            calls.append((system, list(user_messages), kwargs))
            return [m.upper() for m in user_messages]

        stub.batch = real_batch
        result = call_batch(stub, "S", ["a", "b"], tier=config.STANDARD)

        self.assertEqual(result, ["A", "B"])
        self.assertEqual(calls, [("S", ["a", "b"], {"tier": config.STANDARD})])

    def test_the_batch_attribute_only_receives_arguments_it_accepts(self):
        def real_batch(system, user_messages):                # no tier, no **kwargs
            return list(user_messages)

        def stub(system, user):
            return user

        stub.batch = real_batch
        result = call_batch(stub, "S", ["a", "b"], tier=config.STANDARD)
        self.assertEqual(result, ["a", "b"])

    def test_an_empty_list_calls_nothing(self):
        def stub(system, user, **kwargs):
            raise AssertionError("should never be reached for an empty batch")

        self.assertEqual(call_batch(stub, "S", []), [])


if __name__ == "__main__":
    unittest.main()
