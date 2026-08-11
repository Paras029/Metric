"""Three models on one judgement: who is asked what, and in what order.

The value of a council is entirely in the independence of the two workers. Two models shown each
other's work converge, and the agreement that produces is worth nothing -- it is one reading with
a second signature on it. So the thing most worth pinning here is negative: neither worker's
prompt may contain anything from the other, and the reconciler's must contain both.

The second thing is that it degrades. A council is a setting somebody switches on to get a better
answer, and the worst possible behaviour is a run that used to finish now failing because one of
three models was busy. Every failure here has to end in an answer: one worker down leaves the
reconciler with one reading, both down falls back to a single ordinary call, and a reconciler that
does not answer hands back a worker's reading rather than nothing.

Everything is off unless a council is configured, and that is checked too -- a feature that
changes behaviour when it is switched off is a feature nobody can leave switched off.
"""
import json
import os
import unittest
from unittest import mock

from scenario_generator.llm import config, council

_TIER = config.Tier(name="judgement", model="base-model", max_tokens=100,
                    reasoning_effort="high", max_attempts=1)

_ENV = {
    "LLM_COUNCIL_ENABLED": "on",
    "LLM_COUNCIL_WORKERS": "worker-one,worker-two",
    "LLM_COUNCIL_RECONCILER": "reconciler-model",
}


class _Recorder:
    """A completion that records which model each prompt went to."""

    def __init__(self, replies=None, fails=()):
        self.replies = replies or {}
        self.fails = set(fails)
        self.seen = []

    def __call__(self, system, user, tier=None, **kwargs):
        model = getattr(tier, "model", "")
        self.seen.append((model, user))
        if model in self.fails:
            raise RuntimeError(f"{model} is busy")
        return self.replies.get(model, f"reply from {model}")

    def prompts_to(self, model):
        return [user for seen_model, user in self.seen if seen_model == model]

    @property
    def models(self):
        return [model for model, _ in self.seen]


def _with_council(**overrides):
    return mock.patch.dict(os.environ, {**_ENV, **overrides}, clear=False)


class TestItIsOffUntilItIsConfigured(unittest.TestCase):
    def test_no_council_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in _ENV:
                os.environ.pop(key, None)
            self.assertIsNone(council.for_stage("INTAKE_DRAFT"))

    def test_switched_off_it_makes_exactly_the_call_it_always_made(self):
        recorder = _Recorder()
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in _ENV:
                os.environ.pop(key, None)
            reply = council.deliberate(recorder, "sys", "task", stage="INTAKE_DRAFT", tier=_TIER)
        self.assertEqual(recorder.models, ["base-model"])
        self.assertEqual(reply, "reply from base-model")

    def test_a_pass_that_cannot_take_one_never_gets_one(self):
        """Writing a scenario up is made per scenario, hundreds of times, over a small input, and
        a bad one is visible on the page beside its neighbours. Paying three times for that would
        triple the bill of the run to improve the part a person can already fix."""
        with _with_council():
            self.assertIsNone(council.for_stage("WRITER"))
            self.assertIsNone(council.for_stage("MATERIALITY_ASSESS"))

    def test_switched_on_with_nobody_named_falls_back_rather_than_failing(self):
        recorder = _Recorder()
        with _with_council(LLM_COUNCIL_WORKERS="only-one"):
            reply = council.deliberate(recorder, "sys", "task", stage="INTAKE_DRAFT", tier=_TIER)
        self.assertEqual(recorder.models, ["base-model"])
        self.assertTrue(reply)

    def test_one_stage_can_be_switched_on_without_the_others(self):
        with mock.patch.dict(os.environ, {**_ENV, "LLM_COUNCIL_ENABLED": "off",
                                          "LLM_COUNCIL_INTAKE_DRAFT_ENABLED": "on"}, clear=False):
            self.assertIsNotNone(council.for_stage("INTAKE_DRAFT"))
            self.assertIsNone(council.for_stage("REVIEWER_ASSESS"))


class TestItReachesTheFirstPhase(unittest.TestCase):
    """Reading the documents is the judgement everything else inherits.

    A council was originally offered from the intake draft onwards, which is the wrong boundary:
    the intake is drafted *from* the reading, the scenario space from that intake, and nothing
    downstream ever re-reads the documents to check. By the time a council could be switched on,
    the reading it would have improved had already happened.
    """

    def test_every_ingestion_call_site_can_take_one(self):
        with _with_council():
            for stage in ("INGEST_READ", "INGEST_RESOLVE", "INGEST_DIAGRAM_READ",
                          "INGEST_DIAGRAM_SYNTHESIZE", "INGEST_DIAGRAM_REPAIR"):
                self.assertIsNotNone(council.for_stage(stage), stage)

    def test_the_images_reach_every_member_of_a_council(self):
        """A diagram pass sends a picture. Without this, a council over one would ask two models
        to read a diagram and send neither of them the diagram."""
        seen = []

        def complete(system, user, tier=None, images=None, **kwargs):
            seen.append((getattr(tier, "model", ""), images))
            return "reply"

        with _with_council():
            council.deliberate(complete, "sys", "read this diagram", stage="INGEST_DIAGRAM_READ",
                               tier=_TIER, images=[("image/png", b"bytes")])

        self.assertEqual(len(seen), 3)
        for model, images in seen:
            self.assertEqual(images, [("image/png", b"bytes")], model)

    def test_extra_arguments_survive_the_fallback_to_a_single_call(self):
        """Switched off, and with both workers down, the images still have to be sent."""
        seen = []

        def complete(system, user, tier=None, images=None, **kwargs):
            seen.append(images)
            return "reply"

        council.deliberate(complete, "sys", "task", stage="INGEST_DIAGRAM_READ", tier=_TIER,
                           images=[("image/png", b"bytes")])
        self.assertEqual(seen, [[("image/png", b"bytes")]])

    def test_the_ingestion_code_actually_asks_for_a_council(self):
        """The switches exist; this is what makes them do anything."""
        from pathlib import Path

        from scenario_generator.ingest import extraction

        source = Path(extraction.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("council.deliberate"), 4)


class TestTheWorkersAreIndependent(unittest.TestCase):
    """The whole value of the thing. Two models shown each other's work converge, and that
    agreement is one reading with a second signature on it."""

    def test_both_workers_get_the_same_prompt_and_neither_gets_the_others(self):
        recorder = _Recorder({"worker-one": "READING-ONE", "worker-two": "READING-TWO"})
        with _with_council():
            council.deliberate(recorder, "sys", "the task", stage="INTAKE_DRAFT", tier=_TIER)

        first = recorder.prompts_to("worker-one")[0]
        second = recorder.prompts_to("worker-two")[0]
        self.assertEqual(first, "the task")
        self.assertEqual(second, "the task")
        self.assertNotIn("READING-TWO", first)
        self.assertNotIn("READING-ONE", second)

    def test_the_reconciler_gets_the_task_and_both_readings(self):
        recorder = _Recorder({"worker-one": "READING-ONE", "worker-two": "READING-TWO"})
        with _with_council():
            council.deliberate(recorder, "sys", "the task", stage="INTAKE_DRAFT", tier=_TIER)

        prompt = recorder.prompts_to("reconciler-model")[0]
        self.assertIn("the task", prompt)
        self.assertIn("READING-ONE", prompt)
        self.assertIn("READING-TWO", prompt)

    def test_the_reconcilers_answer_is_the_one_used(self):
        recorder = _Recorder({"worker-one": "READING-ONE", "worker-two": "READING-TWO",
                              "reconciler-model": "SETTLED"})
        with _with_council():
            reply = council.deliberate(recorder, "sys", "task", stage="INTAKE_DRAFT", tier=_TIER)
        self.assertEqual(reply, "SETTLED")

    def test_the_workers_go_out_before_the_reconciler(self):
        recorder = _Recorder()
        with _with_council():
            council.deliberate(recorder, "sys", "task", stage="INTAKE_DRAFT", tier=_TIER)
        self.assertEqual(recorder.models[-1], "reconciler-model")
        self.assertEqual(sorted(recorder.models[:2]), ["worker-one", "worker-two"])


class TestItDegradesRatherThanFails(unittest.TestCase):
    def test_one_worker_down_still_reaches_the_reconciler(self):
        recorder = _Recorder({"worker-two": "READING-TWO", "reconciler-model": "SETTLED"},
                             fails=["worker-one"])
        with _with_council():
            reply = council.deliberate(recorder, "sys", "task", stage="INTAKE_DRAFT", tier=_TIER)
        self.assertEqual(reply, "SETTLED")
        self.assertIn("READING-TWO", recorder.prompts_to("reconciler-model")[0])

    def test_both_workers_down_falls_back_to_one_ordinary_call(self):
        recorder = _Recorder(fails=["worker-one", "worker-two"])
        with _with_council():
            reply = council.deliberate(recorder, "sys", "task", stage="INTAKE_DRAFT", tier=_TIER)
        self.assertEqual(reply, "reply from base-model")

    def test_a_reconciler_that_does_not_answer_hands_back_a_reading(self):
        """Better a reading one model actually made than nothing at all."""
        recorder = _Recorder({"worker-one": "READING-ONE"}, fails=["reconciler-model"])
        with _with_council():
            reply = council.deliberate(recorder, "sys", "task", stage="INTAKE_DRAFT", tier=_TIER)
        self.assertEqual(reply, "READING-ONE")


class TestOverABatchedPass(unittest.TestCase):
    """The reviewer and the coverage mapper send many chunks at once, so a council over them is
    three flights rather than three calls per chunk."""

    def _messages(self):
        return ["chunk one", "chunk two", "chunk three"]

    def test_every_chunk_reaches_both_workers_and_the_reconciler(self):
        recorder = _Recorder()
        with _with_council():
            replies = council.deliberate_batch(recorder, "sys", self._messages(),
                                               stage="REVIEWER_ASSESS", tier=_TIER)
        self.assertEqual(len(replies), 3)
        for model in ("worker-one", "worker-two", "reconciler-model"):
            self.assertEqual(len(recorder.prompts_to(model)), 3, model)

    def test_a_chunks_reconciler_prompt_carries_that_chunk_and_no_other(self):
        recorder = _Recorder()
        with _with_council():
            council.deliberate_batch(recorder, "sys", self._messages(),
                                     stage="REVIEWER_ASSESS", tier=_TIER)
        prompts = recorder.prompts_to("reconciler-model")
        self.assertIn("chunk one", prompts[0])
        self.assertNotIn("chunk two", prompts[0])
        self.assertIn("chunk three", prompts[2])

    def test_the_replies_come_back_one_per_chunk_in_order(self):
        def complete(system, user, tier=None, **kwargs):
            if getattr(tier, "model", "") != "reconciler-model":
                return "worker text"
            return json.dumps({"chunk": user.split("\n")[0]})

        with _with_council():
            replies = council.deliberate_batch(complete, "sys", self._messages(),
                                               stage="REVIEWER_ASSESS", tier=_TIER)
        self.assertEqual([json.loads(r)["chunk"] for r in replies],
                         ["chunk one", "chunk two", "chunk three"])

    def test_a_chunk_both_workers_failed_comes_back_as_the_failure(self):
        """Every caller of call_batch already handles that: the chunk is left as the passes
        before it set it, and the reason is logged."""
        def complete(system, user, tier=None, **kwargs):
            model = getattr(tier, "model", "")
            if model.startswith("worker") and "chunk two" in user:
                raise RuntimeError("busy")
            return "text"

        with _with_council():
            replies = council.deliberate_batch(complete, "sys", self._messages(),
                                               stage="REVIEWER_ASSESS", tier=_TIER)
        self.assertIsInstance(replies[1], BaseException)
        self.assertEqual(replies[0], "text")
        self.assertEqual(replies[2], "text")

    def test_switched_off_a_batched_pass_is_one_flight_on_the_stage_model(self):
        recorder = _Recorder()
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in _ENV:
                os.environ.pop(key, None)
            replies = council.deliberate_batch(recorder, "sys", self._messages(),
                                               stage="REVIEWER_ASSESS", tier=_TIER)
        self.assertEqual(len(replies), 3)
        self.assertEqual(set(recorder.models), {"base-model"})


class TestTheStageItRunsAsIsNamed(unittest.TestCase):
    """Every council call carries its own tier name, so the metering that counts calls per stage
    still says which pass they belonged to."""

    def test_worker_and_reconciler_calls_are_labelled(self):
        seen = []

        def complete(system, user, tier=None, **kwargs):
            seen.append(tier.name)
            return "text"

        with _with_council():
            council.deliberate(complete, "sys", "task", stage="INTAKE_DRAFT", tier=_TIER)
        self.assertEqual(sorted(seen), ["judgement:reconciler", "judgement:worker",
                                        "judgement:worker"])


if __name__ == "__main__":
    unittest.main()
