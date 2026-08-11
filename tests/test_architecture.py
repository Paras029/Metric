"""The decisions this tool is built on, checked against the code rather than against a comment.

Each of these is stated somewhere in a module docstring, and each one has already been broken at
least once by an ordinary change made for an ordinary reason. A rule written only in prose is a
rule that holds until the next person has a deadline.
"""
import ast
import pathlib
import re
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGE = _ROOT / "scenario_generator"
_MODULES = sorted(_PACKAGE.rglob("*.py"))


def _string_literals(path: pathlib.Path) -> set:
    """Every string constant in executable code, with docstrings left out.

    Naming a value in prose is documentation; writing it out where it is compared or assigned is
    a second spelling waiting to diverge from the first.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
        # A bare string statement is a trailing attribute docstring, which this package uses.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            docstrings.add(id(node.value))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings}


def _imports(path: pathlib.Path) -> set:
    """Every module this one imports, as dotted names resolved against the package."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    here = path.relative_to(_ROOT).with_suffix("").parts
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = here[:len(here) - node.level]
                module = ".".join(base + tuple((node.module or "").split("."))).strip(".")
            else:
                module = node.module or ""
            # Qualified by what was imported, because "from ..io import sheets" names a module and
            # "from ..core.models import Capability" names a class, and only the first has to be
            # distinguishable from importing the package as a whole.
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


class TestTheLayersOnlyPointOneWay(unittest.TestCase):
    """core is the domain, and it is the part that has to stay testable without any of the rest."""

    def test_core_reaches_for_no_model_and_no_interface(self):
        """core says of itself: "No file I/O beyond the intake workbook itself, and no model
        calls." The intake reader is that exception, and it reaches for the low-level sheet
        helper -- not for the scenario space metadata and pack writers, which are built on top of core."""
        allowed = ("scenario_generator.core", "scenario_generator.utils",
                   "scenario_generator.io.sheets")
        for path in (_PACKAGE / "core").rglob("*.py"):
            for imported in _imports(path):
                if not imported.startswith("scenario_generator"):
                    continue
                self.assertTrue(imported.startswith(allowed),
                                f"{path.name} imports {imported}; core holds the domain and must "
                                f"not depend on the model layer, the interface, or the workbook "
                                f"writers built on top of it")

    def test_utils_depends_on_nothing_of_ours(self):
        """"Pure, dependency-free helpers" -- which is only true while it stays that way."""
        for path in (_PACKAGE / "utils").rglob("*.py"):
            for imported in _imports(path):
                if imported.startswith("scenario_generator"):
                    self.assertTrue(imported.startswith("scenario_generator.utils"), path.name)

    def test_the_interface_holds_no_pipeline_logic_of_its_own(self):
        """Both front ends call the same pipeline functions, which is what stops them drifting
        apart. A runner that built scenarios itself would be a third implementation."""
        runners = (_PACKAGE / "webapp" / "runners.py").read_text(encoding="utf-8")
        for forbidden in ("enumerate_paths(", "instantiate_all(", "DecisionGraph("):
            self.assertNotIn(forbidden, runners,
                             "the interface must call pipeline functions rather than reimplement "
                             "them")

    def test_nothing_outside_the_model_layer_calls_a_model(self):
        """SafeChain is reached through the gateway, so there is one place authentication, retries
        and the request shape are decided."""
        for path in _MODULES:
            if path.parts[-2:] == ("llm", "gateway.py"):
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("import safechain", source, path.name)
            self.assertNotIn("from safechain", source, path.name)


class TestTheAnswerNeverReachesThePack(unittest.TestCase):
    """The one property the whole exercise depends on: the model owner cannot see the key."""

    def test_the_pack_writer_never_reads_a_ground_truth_field(self):
        source = (_PACKAGE / "io" / "workbooks.py").read_text(encoding="utf-8")
        pack = source[source.index("def write_data_template("):]
        pack = pack[:pack.index("\ndef ", 1)] if "\ndef " in pack[1:] else pack
        # Where a run starts is the question and is issued; how it ends is the answer and is not.
        for field in ("termination", "expected_variant", "outcome_type", "owner_coverage",
                      "review_rationale", "materiality_rationale", "review_flag"):
            self.assertNotIn(f".{field}", pack,
                             f"the data template must not carry {field}")

    def test_the_writer_is_never_shown_the_expected_outcome(self):
        """Its output is issued to the model owner, so text written from the answer would
        leak it in prose. The route is a different thing and is shown: the tester has to drive
        each decision to a named outcome, and cannot be asked to do that blind."""
        payload = (_PACKAGE / "llm" / "writer.py").read_text(encoding="utf-8")
        payload = payload[payload.index("def _payload("):]
        payload = payload[:payload.index("\n    def ")]
        self.assertNotIn("termination", payload)
        self.assertNotIn("outcome_type", payload)


class TestOneVocabularyPerThing(unittest.TestCase):
    """A closed list written out by hand in a second place is how the two spellings diverge."""

    def test_no_module_spells_an_origin_as_a_bare_string(self):
        """Scoped to the origins that are vocabulary rather than English.

        "graph" and "probe" are ordinary words that turn up in prose meant for a model, and
        flagging those would be noise. The hyphenated ones cannot appear by coincidence, and they
        are the ones that matter: they are the names a rename has to reach every copy of.
        """
        from scenario_generator.core.models import LEGACY_ORIGINS, ORIGINS

        watched = {o for o in ORIGINS if "-" in o} | set(LEGACY_ORIGINS)
        for path in _MODULES:
            relative = str(path.relative_to(_ROOT))
            if relative == "scenario_generator/core/models.py":
                continue
            for literal in _string_literals(path):
                self.assertNotIn(literal, watched,
                                 f"{relative} writes the origin {literal!r} out by hand; use the "
                                 f"constant in core.models so there is one spelling of it")

    def test_an_origin_an_older_registry_carries_still_reads_as_what_it_meant(self):
        from scenario_generator.core.models import (FUNCTIONAL_ORIGINS, LEGACY_ORIGINS,
                                                    canonical_origin)
        for old, new in LEGACY_ORIGINS.items():
            self.assertEqual(canonical_origin(old), new)
        self.assertIn(canonical_origin("coverage-gap"), FUNCTIONAL_ORIGINS,
                      "a scenario written before the rename must still reach the data template")

    def test_every_origin_the_interface_can_show_has_a_label(self):
        from scenario_generator.core.models import ORIGINS
        from scenario_generator.webapp.scenarios import ORIGIN_LABELS

        self.assertEqual(set(ORIGINS), set(ORIGIN_LABELS),
                         "an unlabelled origin shows its raw slug and cannot be filtered to")


class TestAWorkbookIsNeverWrittenStale(unittest.TestCase):
    """A workbook that is wrong the moment it is written is worse than one that is missing."""

    def test_the_pack_is_not_written_before_materiality_is_assessed(self):
        """Run counts come from materiality. refine() wrote a pack with every scenario still on
        the untouched default, superseded by the next command in the sequence."""
        source = (_PACKAGE / "pipeline.py").read_text(encoding="utf-8")
        body = source[source.index("def refine("):source.index("def assess_materiality(")]
        self.assertNotIn("write_data_template", body)

    def test_the_pack_is_built_from_a_registry_by_a_command_of_its_own(self):
        source = (_PACKAGE / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn("def build_pack(", source)


class TestPromptsAreEditableWithoutTouchingCode(unittest.TestCase):
    def test_every_prompt_the_code_names_exists_as_a_file(self):
        prompts = {p.stem for p in (_PACKAGE / "prompts").glob("*.md")}
        named = set()
        for path in _MODULES:
            named.update(re.findall(r'_PROMPT = "([\w.]+)"',
                                    path.read_text(encoding="utf-8")))
            named.update(re.findall(r'prompt_loader\.(?:load|render)\("([\w.]+)"',
                                    path.read_text(encoding="utf-8")))
        self.assertEqual(named - prompts, set())

    def test_no_module_carries_a_prompt_inline(self):
        """Wording lives in prompts/*.md so someone who does not write Python can change it."""
        for path in _MODULES:
            if path.parts[-2] == "prompts":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if len(node.value) > 600 and "\n" in node.value:
                        self.assertNotIn("You are", node.value[:200],
                                         f"{path.name} carries prompt wording inline")


if __name__ == "__main__":
    unittest.main()
