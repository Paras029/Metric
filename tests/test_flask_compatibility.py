"""The interface must run on whichever Flask the environment already has.

Packages arrive through an internal mirror one approval at a time, so the installed Flask is not
something this project gets to choose. Writing to the older interface costs nothing and removes
a dependency on that.

These tests exist because the development environment will usually have a *newer* Flask than the
one the tool runs on, so a 2.0-only convenience works perfectly here and fails on the machine
that matters. ``@app.post`` shipped exactly that way: every test passed, and the interface would
not start.
"""
import re
import tempfile
import unittest
from pathlib import Path

from scenario_generator.webapp import app as webapp
from scenario_generator.webapp.app import create_app

_SOURCE = Path(webapp.__file__).read_text(encoding="utf-8")

# Flask 2.0 added these as shorthand for app.route(..., methods=[...]). They read better and are
# unusable below that version.
_VERSION_SPECIFIC = re.compile(r"@app\.(get|post|put|delete|patch)\(")


class TestNoVersionSpecificApi(unittest.TestCase):
    def test_routes_avoid_the_flask_2_shortcuts(self):
        found = _VERSION_SPECIFIC.findall(_SOURCE)
        self.assertEqual(
            found, [],
            "app.get/app.post need Flask 2.0. Use app.route(..., methods=[...]) so the "
            "interface starts on whatever version is installed.")

    def test_files_are_sent_by_string_path(self):
        """send_file accepted pathlib only from Flask 2.0."""
        self.assertNotIn("send_file(path,", _SOURCE)


class TestEveryRouteIsReachable(unittest.TestCase):
    """Building the app is what would have caught the shortcut; this pins that it stays built."""

    def setUp(self):
        self.app = create_app(Path(tempfile.mkdtemp()))
        self.client = self.app.test_client()

    def _rules(self):
        return [r for r in self.app.url_map.iter_rules() if r.endpoint != "static"]

    def test_the_application_builds(self):
        self.assertTrue(self._rules())

    def test_the_expected_routes_exist_with_the_expected_methods(self):
        actual = {str(rule): sorted(rule.methods - {"HEAD", "OPTIONS"}) for rule in self._rules()}
        expected = {
            "/": ["GET"],
            "/workspaces": ["POST"],
            "/workspaces/<slug>": ["GET"],
            "/stage/<key>": ["GET"],
            "/stage/<key>/run": ["POST"],
            "/stage/<key>/stop": ["POST"],
            "/stage/<key>/progress": ["GET"],
            "/stage/<key>/note": ["POST"],
            "/stage/<key>/answers": ["POST"],
            "/stage/<key>/upload": ["POST"],
            "/stage/<key>/remove": ["POST"],
            "/stage/<key>/redact": ["POST"],
            "/stage/<key>/diagrams": ["POST"],
            "/stage/<key>/scenario/<scenario_id>": ["POST"],
            "/stage/intake/capability/<capability_id>/span": ["POST"],
            "/stage/intake/decision/<decision_id>/scope": ["POST"],
            "/stage/intake/revise": ["POST"],
            "/stage/intake/structure-review": ["POST"],
            "/stage/intake/structure-review/<proposal_id>/apply": ["POST"],
            "/stage/intake/structure-review/<proposal_id>/dismiss": ["POST"],
            "/stage/coverage/threshold": ["POST"],
            "/stage/summary/scope": ["POST"],
            "/stage/<key>/reset": ["POST"],
            "/stage/<key>/download/<name>": ["GET"],
            "/graph": ["GET"],
            "/template": ["GET"],
        }
        self.assertEqual(actual, expected)

    def test_a_workspace_can_be_created_and_opened(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        created = self.client.post("/workspaces", data={"name": "Compatibility check"})
        self.assertEqual(created.status_code, 302)
        self.assertEqual(self.client.get("/stage/intake").status_code, 200)

    def test_progress_answers_before_anything_has_run(self):
        self.client.post("/workspaces", data={"name": "Compatibility check"})
        body = self.client.get("/stage/intake/progress").get_json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["percent"], 0)


if __name__ == "__main__":
    unittest.main()
