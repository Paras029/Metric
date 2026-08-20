"""The knobs this tool advertises, against the ones it actually reads.

A setting documented in the README and listed in tuning.yml that no call site consults is worse
than no setting: someone turns it down, sees no change, and concludes the tool ignores its own
configuration. That is not hypothetical -- a whole tier and its `LLM_FAST_*` prefix survived the
pass they configured being deleted, and nothing said so.

So the three places a stage can be named are checked against each other here, and against the
call sites that ask for them.
"""
import ast
import os
import pathlib
import re
import tempfile
import unittest
from unittest import mock

import yaml

from metric.llm import config

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGE = _ROOT / "metric"


def _asked_for() -> set:
    """Every stage key some call site names.

    Two forms, because both are in use: most pass the key straight to ``stage_tier`` and friends,
    while ingestion's reading calls go through a helper that takes the key as an argument. The
    second cannot be matched at the call to ``stage_tier``, so any quoted occurrence of a key
    counts -- looser, but it still catches the case that matters, which is a key nothing anywhere
    refers to.
    """
    direct = re.compile(r'stage_(?:tier|batch_size|concurrency)\(\s*"([A-Z_]+)"')
    asked, quoted = set(), set()
    for path in _PACKAGE.rglob("*.py"):
        if path.name == "config.py":
            continue
        source = path.read_text(encoding="utf-8")
        asked.update(direct.findall(source))
        quoted.update(re.findall(r'"([A-Z][A-Z_]+)"', source))
    return asked | (quoted & set(config.STAGE_KEYS))


def _tiers() -> set:
    """Every tier object the config module defines, by the name it was built with."""
    tiers = set()
    for node in ast.parse((_PACKAGE / "llm" / "config.py").read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if (isinstance(value, ast.Call) and getattr(value.func, "id", "") == "_tier"
                and value.args and isinstance(value.args[0], ast.Constant)):
            tiers.add(value.args[0].value)
    return tiers


class TestEveryStageKeyIsReal(unittest.TestCase):
    def setUp(self):
        self.tuned = {k.upper() for k in
                      (yaml.safe_load((_ROOT / "tuning.yml").read_text()).get("stages") or {})}

    def test_the_tuning_file_names_only_stages_the_code_knows(self):
        self.assertEqual(self.tuned - set(config.STAGE_KEYS), set())

    def test_every_stage_the_code_knows_is_in_the_tuning_file(self):
        """The file is the shared, reviewable statement of how the work is run. A key missing
        from it is a knob only someone reading the source would find."""
        self.assertEqual(set(config.STAGE_KEYS) - self.tuned, set())

    def test_every_stage_key_is_asked_for_by_a_call_site(self):
        self.assertEqual(set(config.STAGE_KEYS) - _asked_for(), set())

    def test_every_call_site_asks_for_a_declared_stage_key(self):
        """The other direction: a call site naming a key STAGE_KEYS does not list still works,
        but it is undocumented and nothing will ever mention it."""
        self.assertEqual(_asked_for() - set(config.STAGE_KEYS), set())


class TestEveryTierIsUsed(unittest.TestCase):
    def test_no_tier_is_defined_that_nothing_runs_on(self):
        source = "\n".join(p.read_text(encoding="utf-8") for p in _PACKAGE.rglob("*.py")
                           if p.name != "config.py")
        for tier in _tiers():
            self.assertIn(f"config.{tier.upper()}", source,
                          f"the {tier} tier configures nothing, so its knobs change nothing")


class TestSettingsAreReadWhenTheyAreUsed(unittest.TestCase):
    """The interface runs for hours in one process, so a value fixed at import cannot be changed.

    This was half true and therefore worse than either: the per-stage overrides re-read while the
    model, the tiers, the concurrency cap and whether redaction is on were frozen at import, so
    reload_tuning() took effect for some settings and silently not for others.
    """

    def _reading(self, name):
        return getattr(config, name)

    def test_no_live_setting_is_also_a_module_global(self):
        """A global of the same name would shadow the resolver and never be consulted again."""
        shadowed = sorted(name for name in config._LIVE if name in vars(config))
        self.assertEqual(shadowed, [])

    def test_the_environment_is_read_at_the_moment_the_setting_is_read(self):
        cases = {
            "LLM_MODEL_ID": ("LLM_MODEL_ID", "some-other-model", "some-other-model"),
            "MAX_CONCURRENCY": ("LLM_MAX_CONCURRENCY", "13", 13),
            "DEFAULT_MAX_ATTEMPTS": ("LLM_MAX_ATTEMPTS", "7", 7),
            "MAX_CORPUS_CHARS": ("LLM_MAX_CORPUS_CHARS", "1234", 1234),
            "INGEST_RESOLVE_PASSES": ("LLM_INGEST_RESOLVE_PASSES", "5", 5),
            "PII_REDACTION": ("PII_REDACTION", "on", True),
            "LLM_VISION": ("LLM_VISION", "off", False),
            "MAX_IMAGE_BYTES": ("LLM_MAX_IMAGE_BYTES", "999", 999),
            "DEFAULT_TEMPERATURE": ("LLM_TEMPERATURE", "0.9", 0.9),
        }
        for name, (variable, value, expected) in cases.items():
            before = self._reading(name)
            with mock.patch.dict(os.environ, {variable: value}):
                self.assertEqual(self._reading(name), expected, name)
            self.assertEqual(self._reading(name), before, f"{name} did not go back")

    def test_a_tier_is_rebuilt_from_the_environment_rather_than_from_import(self):
        with mock.patch.dict(os.environ, {"LLM_JUDGEMENT_MAX_TOKENS": "4242",
                                          "LLM_MATERIALITY_MODEL_ID": "something-small"}):
            self.assertEqual(config.JUDGEMENT.max_tokens, 4242)
            self.assertEqual(config.MATERIALITY.model, "something-small")
        self.assertNotEqual(config.JUDGEMENT.max_tokens, 4242)

    def test_reloading_the_tuning_file_takes_effect_for_everything_it_names(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        (directory / "tuning.yml").write_text(
            "concurrency: 9\ndefaults:\n  max_attempts: 6\n"
            "tiers:\n  standard:\n    max_tokens: 111\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TUNING_PATH": str(directory / "tuning.yml")}):
            config.reload_tuning()
            self.assertEqual(config.MAX_CONCURRENCY, 9)
            self.assertEqual(config.DEFAULT_MAX_ATTEMPTS, 6)
            self.assertEqual(config.STANDARD.max_tokens, 111)
        config.reload_tuning()
        self.assertNotEqual(config.STANDARD.max_tokens, 111)

    def test_an_unknown_setting_is_an_error_rather_than_None(self):
        with self.assertRaises(AttributeError):
            config.NOT_A_REAL_SETTING


class TestNothingBindsASettingAtImport(unittest.TestCase):
    def test_no_module_imports_a_live_setting_by_name(self):
        """`from .config import JUDGEMENT` binds once and goes stale -- the exact failure the
        resolvers exist to prevent, and invisible at the call site afterwards."""
        offenders = []
        pattern = re.compile(r"from\s+[.\w]*config\s+import\s+([^\n(]+|\([^)]*\))")
        for path in _PACKAGE.rglob("*.py"):
            if path.name == "config.py":       # where they are defined, and says so in prose
                continue
            for imported in pattern.findall(path.read_text(encoding="utf-8")):
                for name in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", imported):
                    if name in config._LIVE:
                        offenders.append(f"{path}: {name}")
        self.assertEqual(offenders, [])


class TestTheCascadeStillCascades(unittest.TestCase):
    def test_a_stage_with_no_override_is_exactly_its_tier(self):
        stage = config.stage_tier("WRITER", config.STANDARD)
        for field in ("model", "max_tokens", "reasoning_effort", "max_attempts", "temperature"):
            self.assertEqual(getattr(stage, field), getattr(config.STANDARD, field), field)

    def test_a_tier_with_no_override_falls_back_to_the_master_model(self):
        for tier in (config.JUDGEMENT, config.MATERIALITY, config.STANDARD):
            self.assertTrue(tier.model)


if __name__ == "__main__":
    unittest.main()
