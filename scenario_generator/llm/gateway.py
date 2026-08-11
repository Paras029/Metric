"""Model calls, built as LangChain chains.

SafeChain does one thing for this package: given a model name, it returns a LangChain chat model,
already authenticated and already knowing the request body that model expects. That is the whole
of its surface here — one call, ``model(name)``. Everything after it is LangChain, and everything
after it is written to LangChain's documented interfaces rather than to anything SafeChain adds on
top. A wrapper's conveniences change between releases; the Runnable interface underneath does not.

So each call is an ordinary LCEL chain:

    prompt | model | StrOutputParser()

with the pieces doing exactly what their documentation says. ``bind`` fixes the generation
parameters on the model. ``with_retry`` handles a gateway that is busy rather than a request that
is wrong. ``StrOutputParser`` turns the reply message into text, including the case where a model
returns its content as a list of parts. None of that is reimplemented here.

Several passes in this package -- writing scenario text, weighing materiality, mapping the model
owner's scenarios -- split a large scenario space into chunks. ``ask_llm_batch`` sends those chunks
concurrently using the chain's own ``.batch``, which is LangChain's documented way of running one
runnable over many inputs at once rather than a queue this module manages by hand. It needs a
template with real placeholders to do that -- a chain built with the finished text already baked
in, the way ``ask_llm`` builds one, has nothing left to vary between chunks. See
:func:`_batch_prompt` for how that stays safe against the same hazard described below for the
single-call path.

Authentication is SafeChain's problem rather than this module's, which is what keeps three
failure modes out of here entirely: a token expiring in the middle of a long ingestion, a gateway
rejecting a payload shaped for a different provider, and a 401 that means "stale" rather than
"wrong". There is deliberately no hand-rolled fallback path for any of them, because two paths to
the same call is how they drift.

**Prompts are passed as messages, not as templates.** LangChain's tuple form -- ``("system",
text)`` -- runs the text through an f-string parser, and nine of the prompts in this package end
with a JSON output specification. ``{"resolved": [{"question": "..."}]}`` is not a template
variable, and handing it to that parser raises before a single call is made. Substitution happens
in :mod:`scenario_generator.llm.prompt_loader`, which uses a doubled-brace form precisely so that
literal braces survive, and what arrives here is finished text.
"""
from __future__ import annotations

import base64
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .cancellation import Stopped, is_set
from .metering import record_call

logger = logging.getLogger(__name__)

_models: Dict[Tuple, Any] = {}
_lock = threading.Lock()


class ModelUnavailable(RuntimeError):
    """SafeChain could not be loaded, or did not return a usable LangChain model."""


# Where SafeChain's model factory lives, as ``module:attribute``. Several are tried because the
# import path has not been stable across releases and is not something to hardcode from one
# example. Set SAFECHAIN_MODEL_FACTORY to name it outright if your install puts it elsewhere.
FACTORY_ENV = "SAFECHAIN_MODEL_FACTORY"

FACTORY_CANDIDATES: Tuple[str, ...] = (
    "safechain.core_model:model",
    "safechain.core.model:model",
    "safechain.model:model",
    "safechain.models:model",
    "safechain.chat_model:model",
    "safechain:model",
)


def _resolve(path: str):
    """Import ``module:attribute``, or return None if either half is not there."""
    import importlib

    module_name, _, attribute = path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, attribute or "model", None)


def _describe_safechain() -> str:
    """What the installed SafeChain actually exposes, for when none of the candidates fit.

    A "no module named safechain.core_model" tells you the parent package was found and the
    submodule was not, which is a different problem from a missing install and has a different
    fix. Listing what is there turns the next step into reading one line rather than guessing.
    """
    import pkgutil
    import sys
    import types

    try:
        import safechain
    except ImportError:
        return ("The safechain package is not importable at all on this interpreter. Install it "
                "from the internal index, and make sure you are running the Python you installed "
                "it into -- an activated virtual environment uses its own.")

    where = getattr(safechain, "__file__", "an unknown location")

    # Three ways of finding what is inside, because no one of them sees everything: scanning the
    # package directory misses namespace and zipped installs, and the other two only see what has
    # already been imported. Together they cover every shape this has actually arrived in.
    names = {module.name for module in pkgutil.iter_modules(getattr(safechain, "__path__", []))}
    names |= {name for name, value in vars(safechain).items()
              if isinstance(value, types.ModuleType)}
    names |= {name.split(".", 1)[1].split(".")[0] for name in sys.modules
              if name.startswith("safechain.") and sys.modules[name] is not None}

    submodules = sorted(names)
    factories = sorted(name for name, value in vars(safechain).items()
                       if "model" in name.lower() and callable(value))

    return (f"safechain is installed at {where}, so this is the import path rather than the "
            f"install. It contains: {', '.join(submodules) or 'no submodules'}"
            + (f"; and exposes {', '.join(factories)} at the top level" if factories else "")
            + f". Set {FACTORY_ENV} to the right one as module:attribute -- for example "
              f"{FACTORY_ENV}=safechain.core_model:model.")


def _load_safechain():
    """Find SafeChain's model factory, with the environment prepared first.

    Imported here rather than at module load so the command line, the tests and the interface all
    start without it. Only a pass that actually calls a model needs it, which keeps a missing or
    unapproved package from taking down the whole tool.
    """
    config.prepare_environment()
    missing = config.missing_credentials()
    if missing:
        raise ModelUnavailable(
            f"{' and '.join(missing)} not set. SafeChain reads these from the environment; put "
            f"them in .env beside the config.yml that names your models.")

    import os

    named = os.getenv(FACTORY_ENV, "").strip()
    if named:
        factory = _resolve(named)
        if factory is None:
            raise ModelUnavailable(
                f"{FACTORY_ENV} is set to '{named}' and nothing was found there. "
                + _describe_safechain())
        return factory

    for candidate in FACTORY_CANDIDATES:
        factory = _resolve(candidate)
        if factory is not None:
            logger.debug("Using the SafeChain model factory at %s.", candidate)
            return factory

    raise ModelUnavailable("Could not find SafeChain's model factory. " + _describe_safechain())


class SettingRejected(ValueError):
    """A generation parameter this tool set is outside what the model accepts.

    A configuration fault rather than a gateway one, and the distinction matters: a busy gateway
    is worth retrying and this is not. It will fail identically on every attempt, on every call,
    for the whole run.
    """


# What a provider says when a generation parameter is out of range, across the wordings they use.
# Matched loosely on purpose -- the exact phrasing is the provider's to change, and the cost of
# missing one is only that the raw error is shown instead of the plain sentence.
_OUT_OF_RANGE = re.compile(
    r"(max_?output_?tokens|max_?tokens|maxOutputTokens)\b.{0,120}?"
    r"(supported range|must be|range is|between|exceeds|greater than|less than)",
    re.I | re.S)
_RANGE_BOUND = re.compile(r"to\s+(\d{3,})\s*\(exclusive\)|maximum(?: of)?\s+(\d{3,})", re.I)


def _explain(exc: BaseException, tier: Optional["config.Tier"],
             max_tokens: Optional[int]) -> BaseException:
    """The same failure with an actionable first line, where this is one we recognise.

    A provider rejects an out-of-range output cap with a 400 carrying a nested JSON body, and the
    sentence that says what to change is several levels inside it. Every caller logs the exception
    with ``%s``, so what a person sees is that whole body -- and the one thing they need out of it
    is which setting of ours to lower and to what. Anything not recognised is returned untouched:
    guessing at an error nobody has read is how a real fault gets a misleading label.
    """
    text = str(exc)
    if not _OUT_OF_RANGE.search(text):
        return exc

    asked = max_tokens if max_tokens is not None else (tier.max_tokens if tier else None)
    bound = next((g for g in (_RANGE_BOUND.search(text) or ()).groups() or () if g), None) \
        if _RANGE_BOUND.search(text) else None
    ceiling = f" This model's ceiling is {int(bound) - 1:,}." if bound else ""
    where = (f"LLM_{tier.name.split(':')[0].upper()}_MAX_TOKENS, or tiers.{tier.name.split(':')[0]}"
             f".max_tokens in tuning.yml" if tier else "the max_tokens for this call")

    return SettingRejected(
        f"The model refused the output cap this run asked for"
        f"{f' ({asked:,} tokens)' if asked else ''}.{ceiling} Lower it with {where}. "
        f"Retrying will not help -- every call in the run will be refused the same way.\n\n"
        f"The gateway said: {text}")


def _generation_parameters(tier: Optional["config.Tier"], temperature: Optional[float],
                           max_tokens: Optional[int],
                           reasoning_effort: Optional[str]) -> Dict[str, Any]:
    """What the model should generate with. Bound to the model rather than passed per call."""
    parameters: Dict[str, Any] = {
        "temperature": (temperature if temperature is not None
                        else tier.temperature if tier and tier.temperature is not None
                        else config.DEFAULT_TEMPERATURE),
        "max_tokens": (max_tokens if max_tokens is not None
                       else tier.max_tokens if tier else config.STANDARD.max_tokens),
    }
    effort = reasoning_effort or (tier.reasoning_effort if tier
                                  else config.STANDARD.reasoning_effort)
    if effort:
        parameters["reasoning_effort"] = effort
    return parameters


def chat_model(tier: Optional["config.Tier"] = None, model: Optional[str] = None,
               temperature: Optional[float] = None, max_tokens: Optional[int] = None,
               reasoning_effort: Optional[str] = None):
    """A LangChain chat model for this tier, built once and reused.

    ``bind`` and ``with_retry`` are LangChain's own, and are the reason nothing here needs to know
    how SafeChain passes parameters or handles failures. Building reads a config file and sets up
    credentials, so doing it per call would repeat that work on every batch of every pass; the
    cache is keyed on everything that can change the model, so an override still gets its own.
    """
    from langchain_core.runnables import Runnable

    model_id = model or (tier.model if tier else config.LLM_MODEL_ID)
    parameters = _generation_parameters(tier, temperature, max_tokens, reasoning_effort)
    attempts = tier.max_attempts if tier else config.DEFAULT_MAX_ATTEMPTS
    key = (model_id, attempts) + tuple(sorted(parameters.items()))

    with _lock:
        if key in _models:
            return _models[key]

        build = _load_safechain()
        try:
            built = build(model_id)
        except Exception as exc:
            raise ModelUnavailable(
                f"SafeChain could not build '{model_id}' ({exc}). The name has to match one "
                f"declared in the config.yml at CONFIG_PATH.") from exc

        if not isinstance(built, Runnable):
            raise ModelUnavailable(
                f"SafeChain returned {type(built).__name__} for '{model_id}' rather than a "
                f"LangChain runnable. Everything here is built on that interface.")

        _models[key] = built.bind(**parameters).with_retry(
            stop_after_attempt=attempts, wait_exponential_jitter=True)
        return _models[key]


def _image_parts(images: List[Tuple[str, bytes]]) -> List[dict]:
    """Images as content parts, inlined as data URIs so no separate upload step is needed."""
    parts = []
    for media_type, raw in images:
        if len(raw) > config.MAX_IMAGE_BYTES:
            raise ValueError(
                f"an image of {len(raw) // 1000}kB exceeds the {config.MAX_IMAGE_BYTES // 1000}kB "
                f"limit; resize it or raise LLM_MAX_IMAGE_BYTES")
        encoded = base64.b64encode(raw).decode("ascii")
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:{media_type};base64,{encoded}"}})
    return parts


def build_prompt(system_prompt: str, user_message: str,
                 images: Optional[List[Tuple[str, bytes]]] = None):
    """The two messages as a LangChain prompt, with no template variables.

    See the module docstring: the text arriving here is already substituted, and passing it as
    messages rather than as ``("system", text)`` tuples is what stops LangChain reading the JSON
    examples in it as placeholders.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.prompts import ChatPromptTemplate

    content: Any = user_message
    if images:
        content = [{"type": "text", "text": user_message}] + _image_parts(images)

    return ChatPromptTemplate.from_messages([
        SystemMessage(content=system_prompt),
        HumanMessage(content=content),
    ])


def build_chain(system_prompt: str, user_message: str,
                images: Optional[List[Tuple[str, bytes]]] = None,
                tier: Optional["config.Tier"] = None, model: Optional[str] = None,
                temperature: Optional[float] = None, max_tokens: Optional[int] = None,
                reasoning_effort: Optional[str] = None):
    """One call as an LCEL chain: prompt, model, and the reply as text."""
    from langchain_core.output_parsers import StrOutputParser

    return build_prompt(system_prompt, user_message, images) | chat_model(
        tier=tier, model=model, temperature=temperature, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort) | StrOutputParser()


def ask_llm(system_prompt: str, user_message: str,
            temperature: float = None, max_tokens: int = None,
            reasoning_effort: str = None, tier: "config.Tier" = None,
            model: str = None) -> str:
    """Send one system + user prompt and return the reply text.

    Pass a ``tier`` to take its model, output cap and reasoning effort together -- that is how a
    pass says what kind of work it is doing rather than restating three numbers. The individual
    arguments still override it, one call at a time.
    """
    record_call()
    try:
        return build_chain(system_prompt, user_message, tier=tier, model=model,
                           temperature=temperature, max_tokens=max_tokens,
                           reasoning_effort=reasoning_effort).invoke({})
    except Exception as exc:
        raise _explain(exc, tier, max_tokens) from exc


def ask_llm_with_images(system_prompt: str, user_message: str, images: list,
                        temperature: float = None, max_tokens: int = None,
                        reasoning_effort: str = None, tier: "config.Tier" = None,
                        model: str = None) -> str:
    """Send a prompt with images attached, as content parts on the user message.

    ``images`` is a list of (media_type, raw_bytes).

    Raises if vision is switched off, so a caller that reaches here has already decided images are
    worth sending; silently dropping them would produce an answer about nothing.
    """
    if not config.LLM_VISION:
        raise RuntimeError("Vision is disabled (LLM_VISION=off), so images cannot be sent.")

    record_call()
    try:
        return build_chain(system_prompt, user_message, images, tier=tier, model=model,
                           temperature=temperature, max_tokens=max_tokens,
                           reasoning_effort=reasoning_effort).invoke({})
    except Exception as exc:
        raise _explain(exc, tier, max_tokens) from exc


_BATCH_PROMPT = None


def _batch_prompt():
    """The one reusable template every batched call is built from.

    A single placeholder each for the system and human message, and nothing else in the template
    text for LangChain to scan. That is what keeps this safe against the hazard the module
    docstring describes: a template is only parsed for placeholders in its own literal text, never
    in the value substituted into one, so a JSON output specification arriving as the *value* of
    ``{content}`` is inserted exactly as written however many braces it contains. ``ask_llm``
    reaches the same safety a different way -- by never templating at all -- because it has no
    need to vary what it sends between calls; this exists because batching does.

    Built once and cached at module level: the template itself holds no per-call state, only the
    model piped after it changes between tiers.
    """
    global _BATCH_PROMPT
    if _BATCH_PROMPT is None:
        from langchain_core.prompts import (ChatPromptTemplate, HumanMessagePromptTemplate,
                                            SystemMessagePromptTemplate)
        _BATCH_PROMPT = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template("{system}"),
            HumanMessagePromptTemplate.from_template("{content}"),
        ])
    return _BATCH_PROMPT


def ask_llm_batch(system_prompt: str, user_messages: List[str],
                  temperature: float = None, max_tokens: int = None,
                  reasoning_effort: str = None, tier: "config.Tier" = None,
                  model: str = None, max_concurrency: int = None, cancel=None,
                  on_progress=None) -> List[Any]:
    """Send several user messages under one system prompt, concurrently, and return the replies.

    One entry per message, in the same order they were given. An entry is either the reply text
    or the exception raised getting it -- a batch never fails as a whole because one message in
    it did, matching how every caller already treats a single dropped chunk from a JSON reply.
    That is LangChain's own ``return_exceptions``, not something reimplemented here.

    ``max_concurrency`` caps how many of the messages are in flight at once, defaulting to
    ``LLM_MAX_CONCURRENCY`` -- without a cap, a large scenario space split into many chunks would open
    as many connections as it has chunks, which is more than a gateway is necessarily willing to
    hold open at the same time.

    Messages are sent one concurrency-sized wave at a time rather than as a single call to
    LangChain's own ``.batch`` -- which would also cap concurrency, but as one call over the
    whole list, with no point between the first wave and the last where anything here gets to
    look at ``cancel`` before starting the next one. Waving it by hand costs a small amount of
    scheduling efficiency (a wave waits for its slowest message before the next one starts) in
    return for a real place to stop: once ``cancel`` is set, nothing beyond the wave already sent
    is dispatched, and whatever was not gets a :class:`~.cancellation.Stopped` entry instead.

    ``on_progress``, if given, is called with ``(replies so far, replies expected)`` as each wave
    lands. Without it a batched pass is silent for its whole duration and then finishes all at
    once, because every reply arrives before the caller gets any of them -- which is accurate
    about the code and useless to somebody watching a bar that has not moved in four minutes.
    """
    if not user_messages:
        return []

    from langchain_core.output_parsers import StrOutputParser

    chain = _batch_prompt() | chat_model(
        tier=tier, model=model, temperature=temperature, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort) | StrOutputParser()

    concurrency = max_concurrency or config.MAX_CONCURRENCY
    results: List[Any] = []
    for start in range(0, len(user_messages), concurrency):
        if is_set(cancel):
            results.extend(Stopped() for _ in user_messages[start:])
            break
        wave = user_messages[start:start + concurrency]
        record_call(len(wave))
        inputs = [{"system": system_prompt, "content": message} for message in wave]
        # Explained on the way out, the same as a single call. A batch reports a failure as an
        # entry rather than raising it, so without this the one class of error that is worth
        # naming -- a setting the model refuses, which will refuse every other call too -- reaches
        # the log as the provider's raw nested body, once per chunk.
        results.extend(_explain(reply, tier, max_tokens) if isinstance(reply, BaseException)
                       else reply
                       for reply in chain.batch(inputs,
                                                config={"max_concurrency": concurrency},
                                                return_exceptions=True))
        if on_progress is not None:
            on_progress(len(results), len(user_messages))
    return results


# Lets a caller ask "does this completion function support batching?" by looking for this
# attribute rather than by knowing ask_llm is the real gateway -- see calling.call_batch, which
# is how every pass actually reaches this without hardcoding the real function's name.
ask_llm.batch = ask_llm_batch


def reset_models() -> None:
    """Drop the built models, so a configuration change takes effect without a restart."""
    with _lock:
        _models.clear()
