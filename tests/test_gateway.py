"""Building a call as a LangChain chain.

SafeChain is not importable outside the corporate network, so it is stubbed — but the stub is a
real LangChain chat model rather than a duck-typed object, because that is the whole contract this
package relies on. SafeChain returns a Runnable; everything after that is LangChain, and if the
stub were looser these tests would pass while the real thing failed.

Three things are pinned, all of which would otherwise break quietly.

Braces. Every prompt in this package ends with a JSON output specification, and LangChain's tuple
form -- ``("system", text)`` -- runs its text through an f-string parser, which reads
``{"resolved": [...]}`` as a malformed placeholder and raises. Passing messages instead is what
makes the prompts survive, and there is nothing in the type system to stop someone "tidying" it
back to tuples.

Generation parameters. They are bound to the model with ``bind``, so a chain built once carries
its own cap and effort. Losing the bind would leave the model running on its configured defaults
while every tier looked correctly configured.

The chain shape. ``prompt | model | StrOutputParser()`` — the parser is what turns a reply message
into text, including the case where content comes back as a list of parts.
"""
import itertools
import sys
import threading
import types
import unittest
from unittest import mock

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from scenario_generator.llm import config, gateway, metering
from scenario_generator.llm.cancellation import Stopped


class _FakeChatModel(GenericFakeChatModel):
    """A real LangChain chat model standing in for what SafeChain returns.

    Records what it was invoked with, so a test can look at the messages that actually reached the
    model rather than at the prompt that was meant to.
    """

    model_id: str = ""
    seen: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append((messages, kwargs))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _model(model_id="stub", reply="the reply"):
    return _FakeChatModel(messages=itertools.cycle([AIMessage(content=reply)]),
                          model_id=model_id, seen=[])


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
    def setUp(self):
        self.built = []

        def build(model_id):
            self.built.append(_model(model_id))
            return self.built[-1]

        self.env = mock.patch.dict("os.environ", {
            config.CONSUMER_SECRET: "c2VjcmV0", config.CONSUMER_INTEGRATION_ID: "app-1"})
        self.env.start()
        self.patch = _install(build)
        self.addCleanup(gateway.reset_models)
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.env.stop)


class TestThePromptSurvives(_WithSafeChain):
    _JSON = 'Return ONLY: {"resolved": [{"question": "...", "status": "answered"}]}'

    def test_a_json_output_specification_is_not_read_as_a_placeholder(self):
        """The failure this guards raises before a single call is made."""
        self.assertEqual(gateway.build_prompt("You judge.", self._JSON).input_variables, [])

    def test_the_prompt_reaches_the_model_exactly_as_written(self):
        gateway.ask_llm("You judge.", self._JSON)
        messages, _ = self.built[0].seen[0]

        self.assertEqual(messages[0].content, "You judge.")
        self.assertEqual(messages[1].content, self._JSON)

    def test_doubled_braces_are_left_alone_too(self):
        """Substitution happens in the prompt loader; anything left here is literal."""
        gateway.ask_llm("s", "a {{placeholder}} and a {brace}")
        messages, _ = self.built[0].seen[0]
        self.assertEqual(messages[1].content, "a {{placeholder}} and a {brace}")


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


class TestTheChain(_WithSafeChain):
    def test_a_call_returns_the_reply_as_text(self):
        self.assertEqual(gateway.ask_llm("s", "u"), "the reply")

    def test_the_chain_is_prompt_then_model_then_parser(self):
        steps = gateway.build_chain("s", "u").steps
        self.assertEqual([type(step).__name__ for step in [steps[0], steps[-1]]],
                         ["ChatPromptTemplate", "StrOutputParser"])
        self.assertIsInstance(steps[1], Runnable)

    def test_a_reply_returned_as_content_parts_is_still_text(self):
        """Some models answer with a list of parts; StrOutputParser is what flattens it."""
        parts = AIMessage(content=[{"type": "text", "text": "part one "},
                                   {"type": "text", "text": "part two"}])
        patch = _install(lambda model_id: _FakeChatModel(
            messages=itertools.cycle([parts]), model_id=model_id, seen=[]))
        self.addCleanup(patch.stop)

        self.assertEqual(gateway.ask_llm("s", "u"), "part one part two")


class _EchoModel(GenericFakeChatModel):
    """Replies with whatever human message it was given, and remembers every one it saw.

    A test can tell which output answers which input this way even though .batch() runs the
    calls concurrently and they can finish in any order -- unlike a model that always returns
    the same fixed reply, this lets order-independent assertions still be exact ones.
    """

    seen: list = []
    fails_on: str = ""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        content = messages[1].content
        self.seen.append(content)
        if content == self.fails_on:
            raise RuntimeError("boom")
        from langchain_core.outputs import ChatGeneration, ChatResult
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


def _echo(fails_on: str = "") -> _EchoModel:
    return _EchoModel(messages=iter([]), seen=[], fails_on=fails_on)


class TestBatching(_WithSafeChain):
    def test_one_reply_comes_back_per_message_in_the_same_order(self):
        with _install(lambda model_id: _echo()):
            replies = gateway.ask_llm_batch("s", ["one", "two", "three"], tier=config.STANDARD)
        self.assertEqual(list(replies), ["one", "two", "three"])

    def test_every_message_actually_reached_the_model(self):
        model = _echo()
        with _install(lambda model_id: model):
            gateway.ask_llm_batch("s", ["alpha", "beta", "gamma"], tier=config.STANDARD)
        self.assertEqual(sorted(model.seen), ["alpha", "beta", "gamma"])

    def test_an_empty_list_is_not_sent_anywhere(self):
        self.assertEqual(gateway.ask_llm_batch("s", []), [])

    def test_a_json_output_specification_survives_inside_a_batch(self):
        """The hazard the single-call path avoids by not templating at all; batching must avoid
        it a different way, since it has to vary the message between calls."""
        hazardous = 'Return ONLY: {"resolved": [{"question": "...", "status": "answered"}]}'
        with _install(lambda model_id: _echo()):
            replies = gateway.ask_llm_batch("s", [hazardous], tier=config.STANDARD)
        self.assertEqual(replies[0], hazardous)

    def test_one_failure_does_not_lose_the_others(self):
        # The failing item is retried through the same with_retry every call carries, which is
        # correct -- a transient failure inside a batch deserves the same second chance a lone
        # call gets -- but it means real backoff delay unless sleep is short-circuited here.
        with _install(lambda model_id: _echo(fails_on="bad")), mock.patch("time.sleep"):
            replies = gateway.ask_llm_batch("s", ["good-1", "bad", "good-2"], tier=config.STANDARD)

        self.assertEqual(replies[0], "good-1")
        self.assertIsInstance(replies[1], BaseException)
        self.assertEqual(replies[2], "good-2")

    def test_the_model_is_shared_with_the_single_call_path(self):
        """Batching does not bypass the tier cache and build a second model for the same tier."""
        gateway.ask_llm("s", "u", tier=config.STANDARD)
        gateway.ask_llm_batch("s", ["u1", "u2"], tier=config.STANDARD)
        self.assertEqual(len(self.built), 1)

    def test_ask_llm_advertises_its_own_batch_function(self):
        """This is the hook calling.call_batch looks for to find real concurrency."""
        self.assertIs(gateway.ask_llm.batch, gateway.ask_llm_batch)

    def test_cancelling_between_waves_stops_further_sends(self):
        """Messages go out one concurrency-sized wave at a time exactly so there is a point
        between waves to notice a stop -- with concurrency 1 here, that point is every message."""
        event = threading.Event()

        class _CancellingEcho(_EchoModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
                if messages[1].content == "one":            # as if a stop landed right after
                    event.set()
                return result

        with _install(lambda model_id: _CancellingEcho(messages=iter([]), seen=[], fails_on="")):
            replies = gateway.ask_llm_batch("s", ["one", "two", "three"], tier=config.STANDARD,
                                            max_concurrency=1, cancel=event)

        self.assertEqual(replies[0], "one")
        self.assertIsInstance(replies[1], Stopped)
        self.assertIsInstance(replies[2], Stopped)


class TestCallsAreCounted(_WithSafeChain):
    """What a stage spent, reported when it finishes. Counted at the point a request is actually
    sent, so a pass driven by a test stub counts nothing -- there was no call to count."""

    def test_a_single_call_counts_once(self):
        with metering.counted() as calls:
            gateway.ask_llm("s", "u")
        self.assertEqual(calls(), 1)

    def test_a_batch_counts_once_per_message(self):
        with _install(lambda model_id: _echo()):
            with metering.counted() as calls:
                gateway.ask_llm_batch("s", ["one", "two", "three"], tier=config.STANDARD)
        self.assertEqual(calls(), 3)

    def test_an_empty_batch_counts_nothing(self):
        with metering.counted() as calls:
            gateway.ask_llm_batch("s", [])
        self.assertEqual(calls(), 0)

    def test_a_retry_is_not_counted_as_another_call(self):
        """The interesting number is how much work a stage asked for, not how many times the
        transport had to ask for it."""
        with _install(lambda model_id: _echo(fails_on="u")), mock.patch("time.sleep"):
            with metering.counted() as calls:
                with self.assertRaises(Exception):
                    gateway.ask_llm("s", "u", tier=config.JUDGEMENT)
        self.assertEqual(calls(), 1)
        self.assertGreater(config.JUDGEMENT.max_attempts, 1)   # it really did retry

    def test_counting_windows_do_not_leak_into_each_other(self):
        with metering.counted() as first:
            gateway.ask_llm("s", "u")
        with metering.counted() as second:
            pass
        self.assertEqual(first(), 1)
        self.assertEqual(second(), 0)


class TestImages(_WithSafeChain):
    def test_an_image_is_attached_as_a_content_part(self):
        gateway.ask_llm_with_images("s", "describe this", [("image/png", b"not-really-a-png")])
        messages, _ = self.built[0].seen[0]
        content = messages[1].content

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
    """Bound to the model with LangChain's own bind, rather than passed per call.

    Every assertion here is on what actually arrived at the model. Checking the binding's own
    attributes instead would pin a private shape -- ``with_retry`` wraps a binding in a way that
    moves them -- and would pass while the parameters went nowhere.
    """

    def _received(self, index=0):
        _, kwargs = self.built[index].seen[0]
        return kwargs

    def test_a_tier_sends_its_cap_and_its_effort(self):
        gateway.ask_llm("s", "u", tier=config.JUDGEMENT)

        self.assertEqual(self.built[0].model_id, config.JUDGEMENT.model)
        self.assertEqual(self._received()["max_tokens"], config.JUDGEMENT.max_tokens)
        self.assertEqual(self._received()["reasoning_effort"],
                         config.JUDGEMENT.reasoning_effort)

    def test_an_explicit_argument_overrides_the_tier(self):
        gateway.ask_llm("s", "u", tier=config.STANDARD, max_tokens=99, temperature=0.9)

        self.assertEqual(self._received()["max_tokens"], 99)
        self.assertEqual(self._received()["temperature"], 0.9)

    def test_each_tier_gets_its_own_model(self):
        """Two tiers can name the same model and still need different caps."""
        gateway.ask_llm("s", "u", tier=config.JUDGEMENT)
        gateway.ask_llm("s", "u", tier=config.STANDARD)

        self.assertEqual(len(gateway._models), 2)
        self.assertEqual({self._received(0)["max_tokens"], self._received(1)["max_tokens"]},
                         {config.JUDGEMENT.max_tokens, config.STANDARD.max_tokens})

    def test_the_same_tier_is_built_once_and_reused(self):
        """Building reads a config file and sets up credentials; doing it per batch is waste."""
        gateway.ask_llm("s", "u", tier=config.STANDARD)
        gateway.ask_llm("s", "u", tier=config.STANDARD)

        self.assertEqual(len(gateway._models), 1)
        self.assertEqual(len(self.built), 1)

    def test_a_tier_with_fewer_attempts_retries_less(self):
        """Materiality's shorter retry ladder is a property of the tier, not a global -- a tier
        that never overrides it still gets the ordinary default."""
        materiality_model = _echo(fails_on="u")
        with _install(lambda model_id: materiality_model), mock.patch("time.sleep"):
            with self.assertRaises(Exception):
                gateway.ask_llm("s", "u", tier=config.MATERIALITY)
        self.assertEqual(len(materiality_model.seen), config.MATERIALITY.max_attempts)

        judgement_model = _echo(fails_on="u")
        with _install(lambda model_id: judgement_model), mock.patch("time.sleep"):
            with self.assertRaises(Exception):
                gateway.ask_llm("s", "u", tier=config.JUDGEMENT)
        self.assertEqual(len(judgement_model.seen), config.JUDGEMENT.max_attempts)
        self.assertLess(len(materiality_model.seen), len(judgement_model.seen))


class TestFindingSafeChainsFactory(unittest.TestCase):
    """Where SafeChain keeps its model factory is not something to hardcode from one example.

    The import path has moved between releases, and the failure it produces is easy to misread:
    "no module named safechain.core_model" means the package was found and the submodule was not,
    which is a different problem from a missing install and has a different fix.
    """

    def setUp(self):
        env = mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "c2VjcmV0",
                                             config.CONSUMER_INTEGRATION_ID: "app-1"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(gateway.reset_models)
        gateway.reset_models()

    def _package(self, **submodules):
        """A safechain package exposing the given submodules, as {name: {attribute: value}}."""
        package = types.ModuleType("safechain")
        package.__path__ = []
        modules = {"safechain": package}
        for name, attributes in submodules.items():
            module = types.ModuleType(f"safechain.{name}")
            for attribute, value in attributes.items():
                setattr(module, attribute, value)
            setattr(package, name, module)
            modules[f"safechain.{name}"] = module
        patch = mock.patch.dict(sys.modules, modules, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        return package

    def test_the_first_candidate_that_exists_is_used(self):
        factory = _model
        self._package(core_model={"model": factory})
        self.assertIs(gateway._load_safechain(), factory)

    def test_a_later_candidate_is_found_too(self):
        """The one the photograph of the onboarding notebook did not show."""
        factory = _model
        self._package(**{"models": {"model": factory}})
        self.assertIs(gateway._load_safechain(), factory)

    def test_an_explicit_path_overrides_the_search(self):
        factory = _model
        self._package(elsewhere={"build": factory}, core_model={"model": lambda i: None})
        with mock.patch.dict("os.environ",
                             {gateway.FACTORY_ENV: "safechain.elsewhere:build"}):
            self.assertIs(gateway._load_safechain(), factory)

    def test_an_explicit_path_that_is_wrong_says_what_is_actually_there(self):
        self._package(llm_factory={"build_model": _model})
        with mock.patch.dict("os.environ", {gateway.FACTORY_ENV: "safechain.nowhere:model"}):
            with self.assertRaises(gateway.ModelUnavailable) as raised:
                gateway._load_safechain()

        message = str(raised.exception)
        self.assertIn("safechain.nowhere:model", message)
        self.assertIn("llm_factory", message)

    def test_an_install_with_no_recognisable_factory_is_diagnosed_not_just_refused(self):
        """Installed but unrecognised is one line to fix; the message has to say which line."""
        self._package(something_else={})
        with self.assertRaises(gateway.ModelUnavailable) as raised:
            gateway._load_safechain()

        message = str(raised.exception)
        self.assertIn("something_else", message)
        self.assertIn(gateway.FACTORY_ENV, message)

    def test_a_missing_install_is_told_apart_from_a_wrong_path(self):
        patch = mock.patch.dict(sys.modules, {"safechain": None}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)

        with self.assertRaises(gateway.ModelUnavailable) as raised:
            gateway._load_safechain()
        self.assertIn("not importable", str(raised.exception))


class TestFailingBeforeItWastesTime(unittest.TestCase):
    def setUp(self):
        gateway.reset_models()
        self.addCleanup(gateway.reset_models)

    def _configured(self):
        env = mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "c2VjcmV0",
                                             config.CONSUMER_INTEGRATION_ID: "app-1"})
        env.start()
        self.addCleanup(env.stop)

    def test_missing_credentials_are_named_rather_than_left_to_a_401(self):
        with mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "",
                                            config.CONSUMER_INTEGRATION_ID: ""}):
            with self.assertRaises(gateway.ModelUnavailable) as raised:
                gateway.ask_llm("s", "u")
        self.assertIn(config.CONSUMER_SECRET, str(raised.exception))

    def test_a_model_safechain_does_not_know_says_where_to_look(self):
        self._configured()

        def build(model_id):
            raise KeyError(model_id)

        self.addCleanup(_install(build).stop)

        with self.assertRaises(gateway.ModelUnavailable) as raised:
            gateway.ask_llm("s", "u", model="not-in-the-config")
        self.assertIn("config.yml", str(raised.exception))

    def test_something_that_is_not_a_langchain_model_is_refused(self):
        """Everything here is built on that interface, so it is checked rather than assumed."""
        self._configured()
        self.addCleanup(_install(lambda model_id: object()).stop)

        with self.assertRaises(gateway.ModelUnavailable) as raised:
            gateway.ask_llm("s", "u")
        self.assertIn("runnable", str(raised.exception).lower())


class TestTheSecretIsPaddedBeforeUse(unittest.TestCase):
    """The portal strips the trailing '=' and the decoder will not accept it that way."""

    def test_a_stripped_secret_is_padded_back_out(self):
        with mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "YWJjZA",
                                            config.CONSUMER_INTEGRATION_ID: "app-1"}):
            import os
            config.prepare_environment()
            self.assertEqual(os.environ[config.CONSUMER_SECRET], "YWJjZA==")

    def test_a_secret_that_is_already_padded_is_left_alone(self):
        with mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "YWJjZA==",
                                            config.CONSUMER_INTEGRATION_ID: "app-1"}):
            import os
            config.prepare_environment()
            self.assertEqual(os.environ[config.CONSUMER_SECRET], "YWJjZA==")

    def test_the_config_path_defaults_rather_than_being_required(self):
        with mock.patch.dict("os.environ", {config.CONSUMER_SECRET: "YWJjZA==",
                                            config.CONSUMER_INTEGRATION_ID: "app-1"}):
            import os
            os.environ.pop(config.CONFIG_PATH, None)
            config.prepare_environment()
            self.assertEqual(os.environ[config.CONFIG_PATH], config.DEFAULT_CONFIG_PATH)


if __name__ == "__main__":
    unittest.main()
