"""Probe library loading, predicate applicability, and the coverage exclusion rule."""
import unittest

from metric.domain.models import (Capability, Decision, IntakeData, Persona, State,
                                            Tool)
from metric.phases.scenario_generator.workflow.probes import (PREDICATES, applicable, build_probes, evaluate,
                                            load_library)


def _intake(capabilities=None, decisions=None, tools=None) -> IntakeData:
    return IntakeData(
        use_case={"Use case name": "Test", "Business objective": "Test objective"},
        personas=[Persona("P1", "Default user", [], True)],
        capabilities=capabilities or [],
        decisions=decisions or [],
        states=[State("S-00", "Start", "Start", [], False)],
        tools=tools or [],
    )


class TestLibrary(unittest.TestCase):
    def test_every_entry_has_the_required_fields(self):
        for probe in load_library():
            self.assertTrue(probe.get("id"), probe)
            self.assertTrue(probe.get("family"), probe)
            self.assertTrue(probe.get("intent"), probe)
            self.assertTrue(probe.get("expectation"), probe)

    def test_ids_are_unique(self):
        ids = [p["id"] for p in load_library()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_entry_names_a_domain_object(self):
        """The library is use-case-agnostic by construction. Domain specifics are the LLM
        writer's job at instantiation, not the library's — otherwise a probe written for one
        use case silently misapplies to the next."""
        banned = ["transaction", "claim", "card", "account", "balance", "payment", "refund",
                  "dispute", "invoice", "loan", "merchant", "cardmember", "financial", "banking",
                  "tax", "insurance", "patient", "policyholder", "booking", "order"]
        for probe in load_library():
            text = " ".join(str(probe.get(field, "")) for field in
                            ("name", "intent", "expectation")).lower()
            found = [word for word in banned if word in text]
            self.assertFalse(found, f"{probe['id']} is domain-specific: {found}")

    def test_every_predicate_is_known(self):
        """An unknown predicate silently drops a probe, so a typo must fail the build."""
        for probe in load_library():
            for term in str(probe.get("applies_when", "always")).split(" and "):
                self.assertIn(term.strip(), PREDICATES, probe["id"])

    def test_turns_are_sane(self):
        for probe in load_library():
            self.assertGreaterEqual(int(probe["turns"]), 1, probe["id"])
            self.assertLessEqual(int(probe["turns"]), 10, probe["id"])


class TestPredicates(unittest.TestCase):
    def test_always_holds_for_a_bare_intake(self):
        self.assertTrue(evaluate("always", _intake()))

    def test_unknown_predicate_is_false_rather_than_true(self):
        """A typo must drop the probe, never apply it everywhere."""
        self.assertFalse(evaluate("nonsense_predicate", _intake()))

    def test_conjunction_requires_every_term(self):
        intake = _intake(capabilities=[Capability("CAP-01", "Auth", "Gating")],
                         tools=[Tool("T", "CAP-01", False)])
        self.assertTrue(evaluate("has_authentication", intake))
        self.assertFalse(evaluate("has_authentication and touches_state_change", intake))

    def test_state_change_comes_from_tools(self):
        intake = _intake(tools=[Tool("Filing API", "CAP-01", True)])
        self.assertTrue(evaluate("touches_state_change", intake))

    def test_persistent_memory_comes_from_input_source(self):
        intake = _intake(decisions=[Decision("DEC-01", "Recall", "CAP-01", "", ["A"],
                                             input_source="Memory-CrossSession")])
        self.assertTrue(evaluate("has_persistent_memory", intake))
        self.assertFalse(evaluate("has_persistent_memory", _intake()))


class TestApplicability(unittest.TestCase):
    def test_a_bare_intake_gets_only_unconditional_probes(self):
        selected = applicable(_intake())
        self.assertTrue(selected)
        self.assertTrue(all(p.get("applies_when", "always") == "always" for p in selected))

    def test_richer_intake_selects_strictly_more(self):
        rich = _intake(
            capabilities=[Capability("CAP-01", "Auth", "Gating"),
                          Capability("CAP-02", "Profile", "PII-handling")],
            decisions=[Decision("DEC-01", "Recall", "CAP-01", "", ["A"],
                                input_source="Memory-CrossSession")],
            tools=[Tool("Filing API", "CAP-01", True)])
        self.assertGreater(len(applicable(rich)), len(applicable(_intake())))


class TestInstantiation(unittest.TestCase):
    def setUp(self):
        self.probes = build_probes(_intake())

    def test_probes_have_no_decision_path(self):
        self.assertTrue(all(not p.path and p.signature == () for p in self.probes))

    def test_probes_are_flagged_by_origin_not_by_empty_path(self):
        """Coverage filters on origin, so it must be set on every probe."""
        self.assertTrue(all(p.origin == "probe" and p.is_probe for p in self.probes))

    def test_expectation_is_carried_as_ground_truth(self):
        self.assertTrue(all(p.termination for p in self.probes))

    def test_ids_are_stable_and_namespaced(self):
        self.assertTrue(all(p.id.startswith("NF-") for p in self.probes))
        self.assertEqual([p.id for p in self.probes],
                         [p.id for p in build_probes(_intake())])

    def test_turn_meta_matches_declared_turns(self):
        library = {p["id"]: p for p in load_library()}
        for probe in self.probes:
            self.assertEqual(len(probe.turn_meta), library[probe.probe_id]["turns"])

    def test_override_wins_over_llm_materiality(self):
        probe = self.probes[0]
        probe.materiality = "Low"
        self.assertEqual(probe.effective_materiality, "Low")
        probe.materiality_override = "High"
        self.assertEqual(probe.effective_materiality, "High")


if __name__ == "__main__":
    unittest.main()
