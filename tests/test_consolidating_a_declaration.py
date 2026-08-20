"""The same thing said twice, in six sheets that reference each other by id.

A drafted intake is filled from prose that references nothing by id, so the model invents the ids
and the duplication follows: "Identity check" as one capability and "Identity checking service"
as another, a persona under two ids because two documents described it in different words.

That was untidy when a capability was a label. It is expensive now that a capability is the block
the scenario space is enumerated over -- two capabilities that are one produce two blocks where
there is one, each walked separately -- and a duplicate persona multiplies the whole space.
"""
import unittest

from metric.phases.scenario_generator.review.consolidate import _key, consolidate


def _declaration(**changes):
    data = {
        "capabilities": [{"id": "CAP-01", "name": "Identity check", "type": "Gating"},
                         {"id": "CAP-02", "name": "Charge verification",
                          "type": "Transactional"}],
        "decisions": [{"id": "DEC-01", "name": "Verify", "capability_id": "CAP-01"},
                      {"id": "DEC-02", "name": "Charge", "capability_id": "CAP-02"}],
        "tools": [{"name": "Ledger", "capability_id": "CAP-02", "changes_state": True}],
        "personas": [{"id": "P1", "name": "Cardmember", "applies_to": "Wants a refund"}],
    }
    data.update(changes)
    return data


class TestNamesThatMeanTheSameThing(unittest.TestCase):
    def test_a_compound_written_one_way_and_two_is_one_name(self):
        self.assertEqual(_key("Cardmember"), _key("Card members"))

    def test_word_order_does_not_matter(self):
        self.assertEqual(_key("Dispute filing"), _key("Filing a dispute"))

    def test_padding_words_do_not_matter(self):
        self.assertEqual(_key("Identity check"), _key("Identity checking service"))

    def test_a_real_distinction_survives(self):
        """The failure that would matter most: folding two blocks the whole space hangs on."""
        self.assertNotEqual(_key("Identity check"), _key("Identify charge"))
        self.assertNotEqual(_key("Charge verification"), _key("Address verification"))


class TestFoldingCapabilities(unittest.TestCase):
    def test_two_capabilities_that_are_one_become_one(self):
        folded, notes = consolidate(_declaration(capabilities=[
            {"id": "CAP-01", "name": "Identity check", "type": "Gating"},
            {"id": "CAP-04", "name": "Identity checking service", "type": ""}]))
        self.assertEqual([c["id"] for c in folded["capabilities"]], ["CAP-01"])
        self.assertTrue(any("CAP-04" in note for note in notes))

    def test_the_survivor_keeps_what_the_loser_had_filled_in(self):
        """Two half-filled duplicates should make one filled row, not one half-filled row."""
        folded, _ = consolidate(_declaration(capabilities=[
            {"id": "CAP-01", "name": "Identity check", "type": ""},
            {"id": "CAP-04", "name": "Identity checking service", "type": "Gating"}]))
        self.assertEqual(folded["capabilities"][0]["type"], "Gating")

    def test_decisions_and_tools_are_repointed_onto_the_survivor(self):
        """A decision left pointing at a merged-away id belongs to no block and is walked by
        nothing, which is a worse outcome than the duplication."""
        folded, _ = consolidate(_declaration(
            capabilities=[{"id": "CAP-01", "name": "Identity check", "type": "Gating"},
                          {"id": "CAP-04", "name": "Identity checking service", "type": ""}],
            decisions=[{"id": "DEC-01", "name": "Verify", "capability_id": "CAP-04"}],
            tools=[{"name": "Ledger", "capability_id": "CAP-04", "changes_state": True}]))
        self.assertEqual(folded["decisions"][0]["capability_id"], "CAP-01")
        self.assertEqual(folded["tools"][0]["capability_id"], "CAP-01")

    def test_two_genuinely_different_capabilities_are_left_alone(self):
        folded, _ = consolidate(_declaration())
        self.assertEqual(len(folded["capabilities"]), 2)


class TestFoldingPersonasAndTools(unittest.TestCase):
    def test_a_persona_under_two_ids_becomes_one(self):
        folded, notes = consolidate(_declaration(personas=[
            {"id": "P1", "name": "Cardmember", "applies_to": "Wants a refund"},
            {"id": "P2", "name": "Card members", "applies_to": ""}]))
        self.assertEqual([p["id"] for p in folded["personas"]], ["P1"])
        self.assertTrue(any("multiplies" in note for note in notes))

    def test_the_same_tool_twice_becomes_one(self):
        folded, _ = consolidate(_declaration(tools=[
            {"name": "Ledger service", "capability_id": "CAP-02", "changes_state": True},
            {"name": "Ledger", "capability_id": "", "changes_state": False}]))
        self.assertEqual(len(folded["tools"]), 1)


class TestWhatItRefusesToDecide(unittest.TestCase):
    """Reported, not merged. Guessing here silently deletes either a block of the scenario space
    or the record of a system being called."""

    def test_a_name_used_as_both_a_tool_and_a_capability_is_reported(self):
        _, notes = consolidate(_declaration(
            tools=[{"name": "Identity check", "capability_id": "CAP-01",
                    "changes_state": False}]))
        self.assertTrue(any("both a tool and a capability" in note for note in notes))

    def test_but_neither_is_deleted(self):
        folded, _ = consolidate(_declaration(
            tools=[{"name": "Identity check", "capability_id": "CAP-01",
                    "changes_state": False}]))
        self.assertEqual(len(folded["tools"]), 1)
        self.assertEqual(len(folded["capabilities"]), 2)

    def test_a_tool_pointing_at_no_capability_is_reported(self):
        _, notes = consolidate(_declaration(tools=[
            {"name": "Ledger", "capability_id": "CAP-99", "changes_state": True}]))
        self.assertTrue(any("do not exist" in note for note in notes))

    def test_a_capability_nothing_branches_on_is_reported(self):
        _, notes = consolidate(_declaration(
            decisions=[{"id": "DEC-01", "name": "Verify", "capability_id": "CAP-01"}]))
        self.assertTrue(any("no decision exercises" in note.lower() for note in notes))

    def test_a_clean_declaration_produces_no_notes_about_folding(self):
        _, notes = consolidate(_declaration())
        self.assertEqual([n for n in notes if "folded" in n.lower()], [])


class TestItRunsOnEveryDraft(unittest.TestCase):
    def test_the_drafter_folds_and_says_so_in_the_review_sheet(self):
        import json
        from metric.phases.intake.intake.drafting import draft_intake

        reply = json.dumps({
            "use_case": {"name": "Disputes"},
            "personas": [{"id": "P1", "name": "Cardmember", "applies_to": "Wants a refund",
                          "is_default": True}],
            "capabilities": [{"id": "CAP-01", "name": "Identity check", "type": "Gating"},
                             {"id": "CAP-04", "name": "Identity checking service",
                              "type": "Gating"}],
            "decisions": [{"id": "DEC-01", "name": "Check", "capability_id": "CAP-04",
                           "outcomes": ["Pass", "Fail"], "inputs": "", "input_source": "User",
                           "max_attempts": 1, "outcome_condition": ""}],
            "states": [{"id": "S-00", "reached_via": "Start", "description": "Opens",
                        "next_decisions": ["DEC-01"], "is_terminal": False, "outcome_type": ""}],
            "tools": [], "confidence": {}, "review_notes": []})

        draft = draft_intake("context", complete=lambda s, u, **k: reply)
        self.assertEqual([c["id"] for c in draft.data["capabilities"]], ["CAP-01"])
        self.assertEqual(draft.data["decisions"][0]["capability_id"], "CAP-01")
        self.assertTrue(any("Folded together" == n.get("field") for n in draft.review_notes))


if __name__ == "__main__":
    unittest.main()
