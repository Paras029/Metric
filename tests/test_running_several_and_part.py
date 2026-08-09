"""The two ways of not running one whole stage at a time.

Both exist because the pipeline is long. Seven stages, most of them minutes each, means someone
who already knows what they want spends an afternoon clicking Run and waiting -- and a benchmark
of three hundred scenarios means fixing four of them costs a full pass over all three hundred.

They pull in opposite directions and each has one way of going badly wrong, which is what these
pin:

**Running several stages** must not carry on past a stage that did not finish, because everything
after it reads what it produced. The one exception is a stage nothing downstream depends on.

**Running part of a benchmark** must not quietly become running all of it. A selection that comes
to nothing is somebody having narrowed to nothing, not somebody asking for three hundred model
calls -- and the failure mode of guessing wrong is expensive rather than merely wrong.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scenario_generator.core.models import (Capability, Decision, IntakeData, Persona, State)
from scenario_generator.core.probes import build_probes
from scenario_generator.llm.materiality import MaterialityAssessor
from scenario_generator.llm.reviewer import ScenarioReviewer
from scenario_generator.llm.selection import narrow
from scenario_generator.llm.writer import ScenarioWriter
from scenario_generator.webapp import runners, runplan
from scenario_generator.webapp.stages import STAGES
from scenario_generator.webapp.workspace import Workspace

_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default", [], True)],
    capabilities=[Capability("CAP-01", "Auth", "Gating")],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"])],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False)],
    tools=[],
)


# A graph with several routes rather than probes, for the tests about comparative judgement:
# peer signals ignore probes entirely, since depth and redundancy say nothing about them. Three of
# its four routes share a category and a capability, which is what makes "how many others are like
# this one" a number that changes if the benchmark is narrowed.
_ROUTE_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default", [], True)],
    capabilities=[Capability("CAP-01", "Auth", "Gating")],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"]),
               Decision("DEC-02", "Check", "CAP-01", "", ["Yes", "No"])],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False),
            State("S-01", "Mid", "Intermediate", ["DEC-02"], False),
            State("S-02", "Done", "Happy path", [], True),
            State("S-03", "Out", "Happy path", [], True)],
    tools=[],
)


def build_routes():
    from scenario_generator.pipeline import build_scenarios
    return build_scenarios(_ROUTE_INTAKE, with_probes=False)


def _ids_in(user: str):
    return [line.split('"id": "')[1].split('"')[0]
            for line in user.splitlines() if '"id": "' in line]


def _scenario_payload(prompt: str):
    """The JSON array of scenarios out of a rendered prompt, so a test can read what was sent.

    Every task prompt embeds exactly one array, written by ``json.dumps(..., indent=2)``, so it is
    the span from the first ``[`` at the start of a line to the last ``]`` at the start of a line.
    """
    lines = prompt.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "[")
    end = len(lines) - next(i for i, line in enumerate(reversed(lines)) if line.strip() == "]") - 1
    return json.loads("\n".join(lines[start:end + 1]))


class _Recorder:
    """A completion function that remembers which scenarios it was asked about."""

    def __init__(self, entry):
        self.entry = entry
        self.seen = []
        self.proposed = False

    def __call__(self, system, user, **kwargs):
        if "proposals" in user.lower():
            self.proposed = True
            return json.dumps({"proposals": []})
        ids = _ids_in(user)
        self.seen += ids
        return json.dumps({i: dict(self.entry) for i in ids})


class TestNarrowing(unittest.TestCase):
    def setUp(self):
        self.scenarios = build_probes(_INTAKE)[:8]

    def test_no_selection_means_the_whole_benchmark(self):
        self.assertEqual(len(narrow(self.scenarios, None)), len(self.scenarios))

    def test_an_id_that_matches_nothing_is_ignored_rather_than_raising(self):
        """The selection comes from a page that may have been open while the registry changed."""
        picked = [self.scenarios[0].id, "SC-does-not-exist"]
        self.assertEqual([s.id for s in narrow(self.scenarios, picked)], [self.scenarios[0].id])

    def test_the_benchmark_order_is_kept_not_the_selection_order(self):
        picked = [self.scenarios[3].id, self.scenarios[1].id]
        self.assertEqual([s.id for s in narrow(self.scenarios, picked)],
                         [self.scenarios[1].id, self.scenarios[3].id])


class TestAPassRunsOnWhatItWasGiven(unittest.TestCase):
    def setUp(self):
        self.scenarios = build_probes(_INTAKE)[:8]
        self.picked = [s.id for s in self.scenarios[:3]]

    def test_the_writer_leaves_every_other_scenario_alone(self):
        stub = _Recorder({"description": "Written.", "turn_plan": "1. Go."})
        ScenarioWriter(complete=stub).write(self.scenarios, _INTAKE, only=self.picked)

        self.assertEqual(sorted(stub.seen), sorted(self.picked))
        untouched = [s for s in self.scenarios if s.id not in self.picked]
        self.assertTrue(all(s.description != "Written." for s in untouched))

    def test_materiality_still_sees_the_whole_benchmark_when_judging_part_of_it(self):
        """The peer signals are what make a tier comparative, and they are about the whole set.

        Computed from the selection instead, one scenario picked out for a re-run would arrive
        saying nothing else in the benchmark is like it, and come back Critical for a reason that
        is a fact about the selection rather than about the agent.
        """
        routes = build_routes()
        sent = []

        def reply(system, user, **kwargs):
            sent.append(user)
            return json.dumps({i: {"materiality": "High", "confidence": "High",
                                   "rationale": "Weighed."} for i in _ids_in(user)})

        # SC-002 is one of three scenarios sharing a category and capability. Judged on its own,
        # the signal it is sent must still say three.
        MaterialityAssessor(complete=reply).assess(routes, _ROUTE_INTAKE, only=["SC-002"])

        payload = _scenario_payload(sent[0])
        self.assertEqual([entry["id"] for entry in payload], ["SC-002"])
        self.assertEqual(payload[0]["similar_scenarios_in_benchmark"], 3)

    def test_the_review_shows_the_whole_digest_while_judging_part(self):
        sent = []

        def reply(system, user, **kwargs):
            sent.append(user)
            if "proposals" in user.lower():
                return json.dumps({"proposals": []})
            return json.dumps({i: {"materiality": "High", "rationale": "Read.", "flag": ""}
                               for i in _ids_in(user)})

        routes = build_routes()
        ScenarioReviewer(complete=reply).review(routes, _ROUTE_INTAKE, only=["SC-002"])

        prompt = "".join(sent)
        self.assertEqual(len(sent), 1, "a subset run made more than the one assessment call")
        for scenario in routes:
            self.assertIn(scenario.id, prompt,
                          f"{scenario.id} was missing from what the review was shown")
        self.assertIn("(4 scenarios)", prompt, "the review was told the wrong benchmark size")

    def test_a_subset_review_proposes_nothing(self):
        """Proposing is a reading of what the benchmark as a whole is missing. It has nothing to
        do with which rows somebody picked out to be re-judged, and fifteen new scenarios landing
        because four were re-read is not what anybody asked for."""
        stub = _Recorder({"materiality": "High", "rationale": "Read.", "flag": ""})
        _, proposals = ScenarioReviewer(complete=stub).review(
            self.scenarios, _INTAKE, only=self.picked)

        self.assertEqual(proposals, [])
        self.assertFalse(stub.proposed, "the proposal call was made on a subset run")

    def test_a_full_review_still_proposes(self):
        stub = _Recorder({"materiality": "High", "rationale": "Read.", "flag": ""})
        ScenarioReviewer(complete=stub).review(self.scenarios, _INTAKE)
        self.assertTrue(stub.proposed)


class TestWhichStagesCanBeNarrowed(unittest.TestCase):
    def test_only_the_stages_that_judge_scenarios_one_at_a_time(self):
        """Reading a document pack, walking the graph and writing the pack are each one
        indivisible piece of work over everything there is, so none of them can be pointed at
        part of a benchmark even in principle."""
        self.assertEqual(set(runners.SUBSET_STAGES), {"text", "materiality", "review"})

    def test_every_subset_stage_actually_accepts_one(self):
        import inspect
        for key in runners.SUBSET_STAGES:
            signature = inspect.signature(runners.RUNNERS[key])
            self.assertIn("only", signature.parameters, f"{key} cannot be narrowed")


class TestThePlan(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.workspace = Workspace.create(self.root, "Plan test")

    def _set(self, **statuses):
        for key, status in statuses.items():
            self.workspace.state(key).status = status

    def test_a_plan_runs_everything_up_to_the_target_and_stops_there(self):
        keys = runplan.plan(self.workspace, "materiality")
        self.assertEqual(keys[-1], "materiality")
        self.assertNotIn("review", keys)

    def test_a_stage_already_complete_is_not_run_again(self):
        """Running through after correcting one thing should redo what the correction invalidated,
        not spend an hour rewriting text that is still current."""
        self._set(documents="complete", intake="complete")
        keys = runplan.plan(self.workspace, "benchmark")
        self.assertEqual(keys, ["benchmark"])

    def test_a_stage_gone_out_of_date_is_run_again(self):
        self._set(documents="complete", intake="stale")
        self.assertIn("intake", runplan.plan(self.workspace, "intake"))

    def test_an_optional_stage_with_nothing_submitted_is_left_out(self):
        """Most engagements submit no transcripts. A plan that stopped dead at the coverage stage
        every time would make the control useless for the ordinary case."""
        keys = runplan.plan(self.workspace, "issue")
        self.assertNotIn("coverage", keys)
        self.assertNotIn("documents", keys)
        self.assertIn("issue", keys)

    def test_a_target_with_nothing_left_to_do_plans_nothing(self):
        for stage in STAGES:
            self.workspace.state(stage.key).status = "complete"
        self.assertEqual(runplan.plan(self.workspace, "issue"), [])

    def test_every_offered_target_says_how_much_it_would_run(self):
        rows = {row["key"]: row["steps"] for row in runplan.targets(self.workspace)}
        self.assertEqual(rows["benchmark"], len(runplan.plan(self.workspace, "benchmark")))

    def test_a_plan_interrupted_by_a_restart_is_not_offered_as_still_running(self):
        """The thread working through the queue died with the process hosting it. A queue nothing
        is serving is a promise the interface would keep making and never keep."""
        self.workspace.state("text").status = "running"
        self.workspace.begin_plan(["text", "materiality"])

        reloaded = Workspace.load(self.workspace.root)   # a fresh process: nothing is live
        self.assertEqual(reloaded.planned(), [])
        self.assertEqual(reloaded.state("text").status, "stopped")


class TestRunningSeveralStagesThroughTheInterface(unittest.TestCase):
    """The sequence end to end, with every stage's work replaced by a stub that records itself."""

    def setUp(self):
        from scenario_generator.webapp.app import create_app

        self.root = Path(tempfile.mkdtemp())
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.client.post("/workspaces", data={"name": "Sequence test"})
        self.ran = []

    def _workspace(self):
        return Workspace.load(self.root / "sequence-test")

    def _stub(self, failing=None):
        """Replace every runner with one that records that it ran. ``failing`` raises instead."""
        def make(key):
            def run(workspace, progress=None, cancel=None, only=None):
                self.ran.append(key)
                if key == failing:
                    raise RuntimeError("nothing usable came back")
                return {"Did": key}
            return run
        return {key: make(key) for key in runners.RUNNERS}

    def _run_through(self, through="issue", failing=None):
        with mock.patch.dict(runners.RUNNERS, self._stub(failing)), \
             mock.patch.dict("scenario_generator.webapp.app.RUNNERS", self._stub(failing)):
            self.client.post("/run-through", data={"through": through})
            deadline = time.time() + 20
            while time.time() < deadline:
                if not self._workspace().run_plan:
                    break
                time.sleep(0.02)
            else:
                self.fail("the sequence never finished")
        return self._workspace()

    def test_the_stages_run_in_pipeline_order(self):
        self._run_through("review")
        self.assertEqual(self.ran, ["intake", "benchmark", "text", "materiality", "review"])

    def test_a_required_stage_that_fails_halts_everything_after_it(self):
        workspace = self._run_through("issue", failing="materiality")

        self.assertNotIn("review", self.ran)
        self.assertNotIn("issue", self.ran)
        self.assertEqual(workspace.state("materiality").status, "failed")
        self.assertEqual(workspace.state("review").status, "locked")

    def test_an_optional_stage_that_fails_does_not(self):
        """Nothing downstream reads what coverage produced -- see stages.required_before -- so
        stranding the pack over unreadable transcripts would be the control failing exactly where
        it is most useful."""
        # Patched in all three places it is bound: the plan decides whether to include the stage,
        # and the sequence decides whether to skip it.
        with mock.patch.object(runners, "has_input", lambda w, k: True), \
             mock.patch("scenario_generator.webapp.runplan.has_input", lambda w, k: True), \
             mock.patch("scenario_generator.webapp.app.has_input", lambda w, k: True):
            workspace = self._run_through("issue", failing="coverage")

        self.assertIn("issue", self.ran)
        self.assertEqual(workspace.state("coverage").status, "failed")
        self.assertEqual(workspace.state("issue").status, "complete")

    def test_the_queue_is_cleared_however_the_sequence_ends(self):
        self.assertEqual(self._run_through("issue", failing="text").run_plan, {})

    def test_stopping_the_running_stage_stops_the_rest_of_the_sequence(self):
        """One Stop button, not one per queued stage. Everything behind a stage somebody stopped
        would have been built on the input they had just decided against."""
        from scenario_generator.llm.cancellation import Stopped

        def make(key):
            def run(workspace, progress=None, cancel=None, only=None):
                self.ran.append(key)
                if key == "benchmark":                    # the one we stop, mid-sequence
                    self.client.post("/stage/benchmark/stop")
                    raise Stopped()
                return {"Did": key}
            return run

        stubs = {key: make(key) for key in runners.RUNNERS}
        with mock.patch.dict(runners.RUNNERS, stubs), \
             mock.patch.dict("scenario_generator.webapp.app.RUNNERS", stubs):
            self.client.post("/run-through", data={"through": "issue"})
            deadline = time.time() + 20
            while time.time() < deadline and self._workspace().run_plan:
                time.sleep(0.02)

        workspace = self._workspace()
        self.assertEqual(self.ran, ["intake", "benchmark"])
        self.assertEqual(workspace.state("benchmark").status, "stopped")
        self.assertEqual(workspace.state("text").status, "locked")
        self.assertEqual(workspace.run_plan, {})

    def test_the_page_can_follow_the_run_to_whichever_stage_is_going_now(self):
        """A page watching the stage it started at has to know where the sequence has got to, or
        it reloads into a finished stage and stops while the work carries on unseen."""
        payload = self.client.get("/stage/intake/progress").get_json()
        self.assertIn("running", payload)
        self.assertIn("queued", payload)


if __name__ == "__main__":
    unittest.main()
