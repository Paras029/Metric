"""The stylesheet parses, and every declaration in it is inside a rule that has a selector.

This exists because of a bug that cost an hour and would have shipped. Editing the stylesheet left
a block of declarations behind with its selector removed:

    .field__label { ... }
      width: 100%;              <- orphaned, no selector
      padding: .5rem .65rem;
      ...
    .field__input {             <- swallowed by the unterminated block above
      width: 100%;

Braces still balanced. The file still loaded. Every other rule still applied. The only symptom was
one text input rendering at its browser-default width on one screen, which reads as a layout
opinion rather than as a broken file -- and the rule it silently ate was the one making every form
field in the product full width.

CSS has no error reporting to lean on here: a browser recovers from a malformed rule by skipping
to the next one it can make sense of, silently and by design. So the check has to be ours.

Deliberately not a full CSS parser. It checks the two structural properties whose failure is
invisible -- balanced braces, and no declaration outside a rule -- and nothing about whether the
values are any good.
"""
import re
import unittest
from pathlib import Path

_WEBAPP = Path(__file__).resolve().parent.parent / "scenario_generator" / "webapp"
_CSS = _WEBAPP / "static" / "app.css"
_TEMPLATES = _WEBAPP / "templates"


def _without_comments(text: str) -> str:
    """The stylesheet with /* ... */ removed, newlines kept so line numbers still mean something."""
    def blank(match):
        return re.sub(r"[^\n]", " ", match.group(0))
    return re.sub(r"/\*.*?\*/", blank, text, flags=re.S)


class TestTheStylesheetParses(unittest.TestCase):
    def setUp(self):
        self.raw = _CSS.read_text(encoding="utf-8")
        self.css = _without_comments(self.raw)

    def test_braces_balance(self):
        self.assertEqual(self.css.count("{"), self.css.count("}"),
                         "unbalanced braces in app.css")

    def test_no_brace_closes_before_it_opens(self):
        depth = 0
        for number, line in enumerate(self.css.split("\n"), start=1):
            depth += line.count("{") - line.count("}")
            self.assertGreaterEqual(depth, 0, f"app.css line {number}: closing brace with "
                                              f"nothing open — {line.strip()!r}")

    def test_every_declaration_sits_inside_a_rule(self):
        """A declaration at depth zero means the selector above it was lost.

        This is the one that caught the real bug. The orphaned block still looked like valid CSS
        line by line; what made it wrong was where it was.
        """
        depth, orphans = 0, []
        for number, line in enumerate(self.css.split("\n"), start=1):
            stripped = line.strip()
            # A declaration is `property: value;` -- and only counts as orphaned outside a rule.
            # At-rules (@media, @font-face) and selectors carry no semicolon at depth zero.
            if depth == 0 and stripped.endswith(";") and ":" in stripped \
                    and not stripped.startswith("@"):
                orphans.append(f"line {number}: {stripped!r}")
            depth += line.count("{") - line.count("}")

        self.assertEqual(orphans, [], "declarations outside any rule — the selector above them "
                                      "was deleted, and the rule below them has been swallowed:\n"
                                      + "\n".join(orphans))

    def test_every_rule_has_a_selector(self):
        """`{` immediately after `}` with nothing between it is a rule with no selector."""
        collapsed = re.sub(r"\s+", " ", self.css)
        self.assertNotIn("} {", collapsed, "a rule in app.css has no selector")


class TestTheTokensAreAllDefined(unittest.TestCase):
    """Every var() names a custom property this file declares.

    A misspelled token is the other silent failure mode: `var(--rule-strng)` is valid CSS, resolves
    to nothing, and the property falls back to its initial value -- so a border quietly disappears
    rather than turning a colour anybody notices.
    """

    def _declared(self, css: str) -> set:
        """Tokens declared in :root.

        Scoped to that block rather than searched for anywhere, which also enforces the rule the
        file states at the top -- every value comes from the scale -- and avoids reading a BEM
        modifier in a selector (`.button--primary:hover`) as a custom property.
        """
        root = re.search(r":root\s*\{(.*?)\n\}", css, flags=re.S)
        self.assertIsNotNone(root, "app.css has no :root block")
        return set(re.findall(r"(--[\w-]+)\s*:", root.group(1)))

    def _set_by_a_template(self) -> set:
        """Custom properties something other than the palette sets: a template inline, or a script.

        A handful of values are not design decisions at all -- how many columns a grid has is a
        property of the data, and how much of the scenario space runs through one arrow is a
        property of the run -- so they belong with the code that knows the number rather than in a
        palette that cannot. Those are still checked, just against their own source: a typo in one
        is the same silent failure as a typo in a token, so it has to be declared *somewhere*.
        """
        found = set()
        for template in _TEMPLATES.rglob("*.html"):
            found |= set(re.findall(r"style=\"(--[\w-]+)\s*:", template.read_text(encoding="utf-8")))
        for script in (_CSS.parent).glob("*.js"):
            found |= set(re.findall(r"setProperty\(\s*['\"](--[\w-]+)",
                                    script.read_text(encoding="utf-8")))
        return found

    def test_no_custom_property_is_used_without_being_declared(self):
        css = _without_comments(_CSS.read_text(encoding="utf-8"))
        used = set(re.findall(r"var\(\s*(--[\w-]+)", css))
        self.assertEqual(used - self._declared(css) - self._set_by_a_template(), set(),
                         "used but declared neither in :root nor by a template")

    def test_no_declared_token_is_unused(self):
        """An unused token is a decision nothing acts on, and it drifts from the ones that do."""
        css = _without_comments(_CSS.read_text(encoding="utf-8"))
        used = set(re.findall(r"var\(\s*(--[\w-]+)", css))
        self.assertEqual(self._declared(css) - used, set(),
                         "declared in :root and never used")


if __name__ == "__main__":
    unittest.main()
