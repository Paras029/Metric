"""Writing, materiality, review and extraction, sending their chunks concurrently.

Nothing in one chunk's answer depends on another's -- writing one scenario's text does not need
to know what another scenario's text turned out to be, and the same is true of a materiality
verdict, a review verdict, and a mapped conversation. So the calls go out together rather than one
at a time, and what these tests pin is that each pass actually asks for that rather than merely
tolerating it: a completion function that offers batching is used through exactly one call per
pass (or per group, for the writer, which uses two prompts), not quietly once per chunk.

The other half of what is pinned is that batching cannot make a partial failure worse. A chunk a
batch dropped, and a chunk whose call inside the batch raised outright, both have to reach the
same individual-refill path a sequential run would have taken -- and everything from an untouched
completion function, the kind every test written before batching existed still passes, has to
keep working exactly as it did.
"""
import json
import unittest
from typing import Callable, List

from scenario_generator.core.models import (BenchmarkScenario, Capability, Decision, IntakeData,
                                            Persona, State, Tool)
from scenario_generator.core.probes import build_probes
from scenario_generator.ingest.conversations import Conversation, Turn
from scenario_generator.llm.conversation_mapping import ConversationMapper
from scenario_generator.llm.materiality import MaterialityAssessor
from scenario_generator.llm.reviewer import ScenarioReviewer
from scenario_generator.llm.writer import ScenarioWriter

# Satisfies every predicate in the probe library, so build_probes returns enough scenarios to
# split into several chunks under each pass's default batch size.
_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default", [], True)],
    capabilities=[Capability("CAP-01", "Auth", "Gating"),
                 Capability("CAP-02", "PII", "PII-Handling")],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"]),
              Decision("DEC-02", "Memory", "CAP-02", "", ["Found"],
                       input_source="Memory-CrossSession"),
              Decision("DEC-03", "Doc", "CAP-02", "", ["Read"], input_source="Document")],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False)],
    tools=[Tool("Verifier", "CAP-01", True)],
)


def _ids_in(user: str) -> List[str]:
    """Every scenario id embedded in a rendered prompt, in the order it appears."""
    return [line.split('"id": "')[1].split('"')[0]
            for line in user.splitlines() if '"id": "' in line]


class _BatchStub:
    """A completion function that answers through its own ``.batch``, the way the real gateway's
    ``ask_llm`` does. A pass that only ever calls the plain single-message form would pass every
    test that exists today and still be sequential; this is what proves it is not.
    """

    def __init__(self, respond: Callable[[str, str], str], fail_when: Callable[[str], bool] = None):
        self.respond = respond
        self.fail_when = fail_when or (lambda user: False)
        self.batch_calls: List[List[str]] = []
        self.solo_calls: List[str] = []

    def __call__(self, system: str, user: str, **kwargs) -> str:
        """The individual-refill path goes through this, never through .batch."""
        self.solo_calls.append(user)
        if self.fail_when(user):
            raise RuntimeError("boom")
        return self.respond(system, user)

    def batch(self, system: str, user_messages, **kwargs) -> list:
        self.batch_calls.append(list(user_messages))
        results = []
        for message in user_messages:
            if self.fail_when(message):
                results.append(RuntimeError("boom"))
                continue
            results.append(self.respond(system, message))
        return results


class TestTheWriterBatches(unittest.TestCase):
    def setUp(self):
        self.scenarios = build_probes(_INTAKE)[:20]

    def _respond(self, system, user):
        return json.dumps({i: {"name": f"Handle for {i}",
                               "description": f"Description for {i}, long enough to pass the "
                                              f"audit that follows the writing.",
                               "turn_plan": "1. Go."}
                           for i in _ids_in(user)})

    def test_one_call_covers_every_chunk_in_a_group(self):
        """Twenty probes at the default batch size of eight is three chunks; all probes share one
        group, so this must be one call carrying three prompts, not three separate calls."""
        stub = _BatchStub(self._respond)
        ScenarioWriter(complete=stub).write(self.scenarios, _INTAKE)
        self.assertEqual(len(stub.batch_calls), 1)
        self.assertEqual(len(stub.batch_calls[0]), 3)

    def test_every_scenario_is_written_regardless_of_which_chunk_it_was_in(self):
        stub = _BatchStub(self._respond)
        ScenarioWriter(complete=stub).write(self.scenarios, _INTAKE)
        for scenario in self.scenarios:
            self.assertIn(f"Description for {scenario.id}", scenario.description)

    def test_a_chunk_the_batch_could_not_reach_is_refilled_individually(self):
        """One chunk's call raises inside the batch; call_batch reports that as the exception
        rather than losing the other chunks, and the scenarios in it still get written, one at a
        time, through the plain call path."""
        first_chunk_ids = {"seen": None}

        def fail_first_chunk(user):
            ids = _ids_in(user)
            if len(ids) > 1:                                # a batched chunk, not a solo refill
                if first_chunk_ids["seen"] is None:
                    first_chunk_ids["seen"] = ids
                return ids == first_chunk_ids["seen"]
            return False

        stub = _BatchStub(self._respond, fail_when=fail_first_chunk)
        ScenarioWriter(complete=stub).write(self.scenarios, _INTAKE)

        for scenario in self.scenarios:
            self.assertIn(f"Description for {scenario.id}", scenario.description)
        self.assertTrue(stub.solo_calls, "the failed chunk's scenarios were never refilled")

    def test_a_plain_function_with_no_batch_attribute_still_works(self):
        """Every stub written before batching existed is exactly this shape."""
        def plain(system, user, **kwargs):
            return self._respond(system, user)

        ScenarioWriter(complete=plain).write(self.scenarios, _INTAKE)
        for scenario in self.scenarios:
            self.assertIn(f"Description for {scenario.id}", scenario.description)


class TestMaterialityBatches(unittest.TestCase):
    def setUp(self):
        self.scenarios = build_probes(_INTAKE)[:25]

    def _respond(self, system, user):
        return json.dumps({i: {"materiality": "High", "confidence": "High", "rationale": "r"}
                           for i in _ids_in(user)})

    def test_one_call_covers_every_chunk(self):
        """Twenty-five scenarios at the default batch size of ten is three chunks."""
        stub = _BatchStub(self._respond)
        MaterialityAssessor(complete=stub).assess(self.scenarios, _INTAKE)
        self.assertEqual(len(stub.batch_calls), 1)
        self.assertEqual(len(stub.batch_calls[0]), 3)

    def test_every_scenario_is_assessed(self):
        stub = _BatchStub(self._respond)
        MaterialityAssessor(complete=stub).assess(self.scenarios, _INTAKE)
        self.assertTrue(all(s.materiality == "High" for s in self.scenarios))

    def test_a_chunk_the_batch_dropped_entirely_is_refilled(self):
        def respond_missing_first_chunk(system, user):
            ids = _ids_in(user)
            if respond_missing_first_chunk.first is None and len(ids) > 1:
                respond_missing_first_chunk.first = set(ids)
                return json.dumps({})                       # this chunk's reply names nobody
            return self._respond(system, user)
        respond_missing_first_chunk.first = None

        stub = _BatchStub(respond_missing_first_chunk)
        MaterialityAssessor(complete=stub).assess(self.scenarios, _INTAKE)
        self.assertTrue(all(s.materiality == "High" for s in self.scenarios))
        self.assertTrue(stub.solo_calls)

    def test_a_plain_function_with_no_batch_attribute_still_works(self):
        def plain(system, user, **kwargs):
            return self._respond(system, user)

        MaterialityAssessor(complete=plain).assess(self.scenarios, _INTAKE)
        self.assertTrue(all(s.materiality == "High" for s in self.scenarios))


class TestReviewerBatchesTheAssessStep(unittest.TestCase):
    """The assess sweep is chunked and batches; propose is a single unchunked call and does not,
    so it must not appear in the batch call count."""

    def setUp(self):
        self.scenarios = build_probes(_INTAKE)[:18]

    def _respond(self, system, user):
        if '"proposals"' in user:
            return json.dumps({"proposals": []})
        return json.dumps({i: {"materiality": "Medium", "rationale": "r", "flag": ""}
                           for i in _ids_in(user)})

    def test_the_assess_step_is_one_call_and_propose_is_a_separate_solo_one(self):
        """Eighteen scenarios at the default batch size of six is three chunks for assess."""
        stub = _BatchStub(self._respond)
        ScenarioReviewer(complete=stub, batch_size=6).review(self.scenarios, _INTAKE)

        self.assertEqual(len(stub.batch_calls), 1)
        self.assertEqual(len(stub.batch_calls[0]), 3)
        self.assertEqual(len(stub.solo_calls), 1)           # the propose call

    def test_every_scenario_is_reviewed(self):
        stub = _BatchStub(self._respond)
        ScenarioReviewer(complete=stub, batch_size=6).review(self.scenarios, _INTAKE)
        self.assertTrue(all(s.review_materiality == "Medium" for s in self.scenarios))

    def test_a_chunk_that_fails_inside_the_batch_is_left_unchanged_not_raised(self):
        """The review pass has no individual refill of its own -- a failed chunk is simply left
        as the first pass set it, and the run must not stop because of it."""
        first = {"ids": None}

        def fail_first_chunk(user):
            ids = _ids_in(user)
            if len(ids) > 1 and first["ids"] is None:
                first["ids"] = ids
                return True
            return False

        stub = _BatchStub(self._respond, fail_when=fail_first_chunk)
        reviewed, proposals = ScenarioReviewer(complete=stub, batch_size=6).review(
            self.scenarios, _INTAKE)

        failed_ids = set(first["ids"])
        self.assertTrue(all(s.review_materiality == "" for s in reviewed if s.id in failed_ids))
        self.assertTrue(all(s.review_materiality == "Medium"
                            for s in reviewed if s.id not in failed_ids))

    def test_a_plain_function_with_no_batch_attribute_still_works(self):
        def plain(system, user, **kwargs):
            return self._respond(system, user)

        ScenarioReviewer(complete=plain, batch_size=6).review(self.scenarios, _INTAKE)
        self.assertTrue(all(s.review_materiality == "Medium" for s in self.scenarios))


class TestConversationMapperBatches(unittest.TestCase):
    """Coverage mapping is the batched pass with the largest inputs, so its chunking matters most.

    Its batch is smaller than everything else's on purpose -- a transcript dwarfs a scenario
    description, and every call has to carry the whole benchmark alongside them.
    """

    def setUp(self):
        self.conversations = [
            Conversation(id=f"C-{n:03d}",
                         turns=[Turn("user", f"I need help with thing number {n}"),
                                Turn("agent", "Done.")])
            for n in range(20)]
        self.scenarios = [BenchmarkScenario(id="SC-001", path_str="DEC-01=Pass",
                                            category="Happy path", materiality="High",
                                            capabilities=[], persona_id="P1", signature=())]

    def _respond(self, system, user):
        return json.dumps({i: {"scenario_id": "SC-001", "confidence": "high", "reason": "mapped"}
                           for i in _ids_in(user)})

    def _map(self, complete):
        return ConversationMapper(complete=complete).map(
            self.conversations, self.scenarios, _INTAKE)

    def test_one_call_covers_every_chunk(self):
        """Twenty conversations at the default batch size of five is four chunks, sent together."""
        stub = _BatchStub(self._respond)
        self._map(stub)
        self.assertEqual(len(stub.batch_calls), 1)
        self.assertEqual(len(stub.batch_calls[0]), 4)

    def test_every_conversation_comes_back_mapped(self):
        results = self._map(_BatchStub(self._respond))
        self.assertEqual(len(results), 20)
        self.assertTrue(all(r.scenario_id == "SC-001" and r.confidence == "high"
                            for r in results))

    def test_a_chunk_the_batch_could_not_reach_is_refilled_individually(self):
        first = {"ids": None}

        def fail_first_chunk(user):
            ids = _ids_in(user)
            if len(ids) > 1 and first["ids"] is None:
                first["ids"] = ids
                return True
            return False

        stub = _BatchStub(self._respond, fail_when=fail_first_chunk)
        results = self._map(stub)

        self.assertTrue(all(r.reason == "mapped" for r in results))
        self.assertTrue(stub.solo_calls)

    def test_a_plain_function_with_no_batch_attribute_still_works(self):
        results = self._map(lambda system, user, **kwargs: self._respond(system, user))
        self.assertTrue(all(r.reason == "mapped" for r in results))


if __name__ == "__main__":
    unittest.main()
