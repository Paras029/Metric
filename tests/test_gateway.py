"""Building a call as a LangChain chain and running it through SafeChain.

SafeChain is not importable outside the corporate network, so it is stubbed here. What that
leaves is exactly what these tests are for: the chain this package builds, and the two things
about it that would break silently.

The first is braces. Every prompt in this package ends with a JSON output specification, and
LangChain's tuple form -- ``("system", text)`` -- runs its text through an f-string parser, which
reads ``{"resolved": [...]}`` as a malformed placeholder and raises. Passing messages instead is
what makes the prompts survive, and there is nothing in the type system to stop someone
"tidying" it back to tuples.

The second is generation parameters. SafeChain takes them at construction rather than at call
time, and which argument carries them has moved between releases. Passing them the wrong way
fails silently: the model builds, runs, and quietly ignores the token cap.
"""
import sys
import types
import unittest
from unittest import mock

from langchain_core.runnables import Runnable

from scenario_generator.llm import config, gateway


class _Reply:
    def __init__(self, content):
        self.content = content


class _FakeModel(Runnable):
    """Stands in for what SafeChain returns: a LangChain runnable.

    A real Runnable rather than a duck-typed object, because ``prompt | model`` refuses anything
    else -- which is the thing being tested.
    """

    def __init__(self, model_id, parameters=None):
        self.model_id = model_id
        self.parameters = dict(parameters or {})
        self.seen = []
        self.retry = {}

    def invoke(self, value, config=None, **kwargs):
        self.seen.append(value)
        return _Reply("the reply")

    def bind(self, **kwargs):
        self.parameters.update(kwargs)
        return self

    def with_retry(self, **kwargs):
        self.retry = kwargs
        return self


def _install(build):
    """Put a stub SafeChain on the import path and clear anything already built."""
    module = types.ModuleType("safechain.core_model")
    module.model = build
    package = types.ModuleType("safechain")
    package.core_model = module
    patch = mock.patch.dict(sys.modules, {"safechain": package,
                                          "safechain.core_model": module})
    patch.start()
    gateway.reset_models()
    return patch


class _WithSafeChain(unittest.TestCase):
    build = staticmethod(lambda model_id, model_kwargs=None: _FakeModel(model_id, model_kwargs))

    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            config.CONSUMER_SECRET: "c2VjcmV0", config.CONSUMER_INTEGRATION_ID: "app-1"})
        self.env.start()
        self.patch = _install(type(self).build)
        self.addCleanup(gateway.reset_models)
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.env.stop)


class TestThePromptSurvives(_WithSafeChain):
    _JSON = 'Return ONLY: {"resolved": [{"question": "...", "status": "answered"}]}'

    def test_a_json_output_specification_is_not_read_as_a_placeholder(self):
        """The failure this guards raises before a single call is made."""
        prompt = gateway.build_prompt("You judge.", self._JSON)
        self.assertEqual(prompt.input_variables, [])

    def test_the_prompt_reaches_the_model_exactly_as_written(self):
        messages = gateway.build_prompt("You judge.", self._JSON).invoke({}).to_messages()
        self.assertEqual(messages[0].content, "You judge.")
        self.assertEqual(messages[1].content, self._JSON)

    def test_doubled_braces_are_left_alone_too(self):
        """Substitution happens in the prompt loader; anything left here is literal."""
        messages = gateway.build_prompt("s", "a {{placeholder}} and a {brace}").invoke({}) \
            .to_messages()
        self.assertEqual(messages[1].content, "a {{placeholder}} and a {brace}")

    def test_a_call_returns_the_reply_text(self):
        self.assertEqual(gateway.ask_llm("You judge.", self._JSON), "the reply")


class TestTheWholePromptLibrarySurvives(_WithSafeChain):
    """Not a sample: every prompt this package ships, built as a chain.

    Nine of the twenty-two would fail outright under the tuple form, because a JSON output
    specification is not a template. This is the test that would catch someone converting them.
    """

    def test_every_prompt_builds_with_nothing_read_as_a_placeholder(self):
        from scenario_generator.llm import prompt_loader

        for name in prompt_loader.available():
            slots = prompt_loader.placeholders(name)
            text = (prompt_loader.render(name, **{s: "x" for s in slots}) if slots
                    else prompt_loader.load(name))
            prompt = gateway.build_prompt("system text", text)

            self.assertEqual(prompt.input_variables, [], f"{name} leaked a placeholder")
            self.assertEqual(prompt.invoke({}).to_messages()[1].content, text, name)


class TestImages(_WithSafeChain):
    def test_an_image_is_attached_as_a_content_part(self):
        prompt = gateway.build_prompt("s", "describe this", [("image/png", b"not-really-a-png")])
        content = prompt.invoke({}).to_messages()[1].content

        self.assertEqual(content[0], {"type": "text", "text": "describe this"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_an_oversized_image_is_refused_with_a_reason(self):
        with mock.patch.object(config, "MAX_IMAGE_BYTES", 10):
            with self.assertRaises(ValueError) as raised:
                gateway.build_prompt("s", "u", [("image/png", b"x" * 100)])
        self.assertIn("LLM_MAX_IMAGE_BYTES", str(raised.exception))

    def test_images_are_refused_outright_where_vision_is_off(self):
        with mock.patch.object(config, "LLM_VISION", False):
            with self.assertRaises(RuntimeError) as raised:
                gateway.ask_llm_with_images("s", "u", [("image/png", b"x")])
        self.assertIn("LLM_VISION", str(raised.exception))


class TestGenerationParameters(_WithSafeChain):
    def test_a_tier_sets_the_cap_and_the_effort_at_construction(self):
        gateway.ask_llm("s", "u", tier=config.JUDGEMENT)
        built = next(iter(gateway._models.values()))

        self.assertEqual(built.model_id, config.JUDGEMENT.model)
        self.assertEqual(built.parameters["max_tokens"], config.JUDGEMENT.max_tokens)
        self.assertEqual(built.parameters["reasoning_effort"], config.JUDGEMENT.reasoning_effort)

    def test_an_explicit_argument_overrides_the_tier(self):
        gateway.ask_llm("s", "u", tier=config.FAST, max_tokens=99, temperature=0.9)
        built = next(iter(gateway._models.values()))
        self.assertEqual(built.parameters["max_tokens"], 99)
        self.assertEqual(built.parameters["temperature"], 0.9)

    def test_each_tier_gets_its_own_model(self):
        """Two tiers can name the same model and still need different caps."""
        gateway.ask_llm("s", "u", tier=config.JUDGEMENT)
        gateway.ask_llm("s", "u", tier=config.FAST)

        self.assertEqual(len(gateway._models), 2)
        caps = {m.parameters["max_tokens"] for m in gateway._models.values()}
        self.assertEqual(caps, {config.JUDGEMENT.max_tokens, config.FAST.max_tokens})

    def test_the_same_tier_is_built_once_and_reused(self):
        """Construction reads a config file and sets up credentials; doing it per batch is waste."""
        gateway.ask_llm("s", "u", tier=config.STANDARD)
        gateway.ask_llm("s", "u", tier=config.STANDARD)
        self.assertEqual(len(gateway._models), 1)

    def test_retries_are_asked_for_where_the_runnable_supports_them(self):
        gateway.ask_llm("s", "u", tier=config.STANDARD)
        built = next(iter(gateway._models.values()))
        self.assertEqual(built.retry["stop_after_attempt"], gateway.MAX_ATTEMPTS)


class TestHowParametersAreHandedOver(unittest.TestCase):
    """Which argument carries them has moved between SafeChain releases, so it is asked for."""

    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            config.CONSUMER_SECRET: "c2VjcmV0", config.CONSUMER_INTEGRATION_ID: "app-1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(gateway.reset_models)

    def _built(self, build):
        patch = _install(build)
        self.addCleanup(patch.stop)
        gateway.ask_llm("s", "u", tier=config.FAST)
        return next(iter(gateway._models.values()))

    def test_model_kwargs_is_used_where_it_exists(self):
        built = self._built(lambda model_id, model_kwargs=None: _FakeModel(model_id, model_kwargs))
        self.assertEqual(built.parameters["max_tokens"], config.FAST.max_tokens)

    def test_another_name_for_the_same_thing_is_found(self):
        built = self._built(lambda model_id, parameters=None: _FakeModel(model_id, parameters))
        self.assertEqual(built.parameters["max_tokens"], config.FAST.max_tokens)

    def test_a_model_taking_none_of_them_is_bound_instead(self):
        """LangChain's own way of fixing call arguments on a runnable."""
        built = self._built(lambda model_id: _FakeModel(model_id))
        self.assertEqual(built.parameters["max_tokens"], config.FAST.max_tokens)


class TestFailingBeforeItWastesTime(unittest.TestCase):
    def setUp(self):
        gateway.reset_models()
        self.addCleanup(gateway.reset_models)

    def test_missing_credentials_are_named_rather_than_left_to_a_401(self):
        with mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "",
                                            config.CONSUMER_INTEGRATION_ID: ""}):
            with self.assertRaises(gateway.ModelUnavailable) as raised:
                gateway.ask_llm("s", "u")
        self.assertIn(config.CONSUMER_SECRET, str(raised.exception))

    def test_a_model_safechain_does_not_know_says_where_to_look(self):
        env = mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "c2VjcmV0",
                                             config.CONSUMER_INTEGRATION_ID: "app-1"})
        env.start()
        self.addCleanup(env.stop)

        def build(model_id, model_kwargs=None):
            raise KeyError(model_id)

        patch = _install(build)
        self.addCleanup(patch.stop)

        with self.assertRaises(gateway.ModelUnavailable) as raised:
            gateway.ask_llm("s", "u", model="not-in-the-config")
        self.assertIn("config.yml", str(raised.exception))


class TestTheSecretIsPaddedBeforeUse(unittest.TestCase):
    """The portal strips the trailing '=' and the decoder will not accept it that way."""

    def test_a_stripped_secret_is_padded_back_out(self):
        with mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "YWJjZA",
                                            config.CONSUMER_INTEGRATION_ID: "app-1"}):
            config.prepare_environment()
            import os
            self.assertEqual(os.environ[config.CONSUMER_SECRET], "YWJjZA==")

    def test_a_secret_that_is_already_padded_is_left_alone(self):
        with mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "YWJjZA==",
                                            config.CONSUMER_INTEGRATION_ID: "app-1"}):
            config.prepare_environment()
            import os
            self.assertEqual(os.environ[config.CONSUMER_SECRET], "YWJjZA==")

    def test_the_config_path_defaults_rather_than_being_required(self):
        with mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "YWJjZA==",
                                            config.CONSUMER_INTEGRATION_ID: "app-1"}, clear=False):
            import os
            os.environ.pop(config.CONFIG_PATH, None)
            config.prepare_environment()
            self.assertEqual(os.environ[config.CONFIG_PATH], config.DEFAULT_CONFIG_PATH)


if __name__ == "__main__":
    unittest.main()
