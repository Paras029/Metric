import unittest

from metric.shared.json_parsing import parse_json_object


class TestParseJsonObject(unittest.TestCase):
    def test_fenced_with_prose_and_literal_newline(self):
        # Reproduces the real failure: markdown fence, surrounding prose, and a literal
        # newline inside a string value (which strict JSON rejects).
        reply = (
            'Sure, here you go:\n```json\n'
            '{"SC-001": {"description": "ok", "turn_plan": "Step 1.\nStep 2."}}\n'
            '```\nHope that helps!'
        )
        parsed = parse_json_object(reply)
        self.assertIn("SC-001", parsed)
        self.assertIn("\n", parsed["SC-001"]["turn_plan"])

    def test_salvages_partial_batch(self):
        # One malformed entry (missing comma) must not sink the well-formed ones.
        reply = ('{"SC-001": {"description": "ok" "bad": true}, '
                 '"SC-002": {"description": "good"}}')
        parsed = parse_json_object(reply)
        self.assertEqual(list(parsed), ["SC-002"])

    def test_raises_when_nothing_recoverable(self):
        with self.assertRaises(ValueError):
            parse_json_object("no json here at all")


if __name__ == "__main__":
    unittest.main()
