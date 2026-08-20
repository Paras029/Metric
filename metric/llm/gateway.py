"""Model calls, built as LangChain chains."""
from __future__ import annotations

import base64
import logging
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from metric.llm import config
from metric.llm.cancellation import Stopped, is_set
from metric.llm.metering import record_call

logger = logging.getLogger(__name__)

_models: Dict[Tuple, Any] = {}
_built: Dict[str, Any] = {}
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
    """What the installed SafeChain actually exposes, for when none of the candidates fit."""
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
    """Find SafeChain's model factory, with the environment prepared first."""
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


def _built_model(model_id: str):
    """The model SafeChain builds, cached, before anything is bound to it."""
    from langchain_core.runnables import Runnable

    with _lock:
        if model_id in _built:
            return _built[model_id]

        try:
            made = _load_safechain()(model_id)
        except Exception as exc:
            raise ModelUnavailable(
                f"SafeChain could not build '{model_id}' ({exc}). The name has to match one "
                f"declared in the config.yml at CONFIG_PATH.") from exc

        if not isinstance(made, Runnable):
            raise ModelUnavailable(
                f"SafeChain returned {type(made).__name__} for '{model_id}' rather than a "
                f"LangChain runnable. Everything here is built on that interface.")

        _built[model_id] = made
        return made


def chat_model(tier: Optional["config.Tier"] = None, model: Optional[str] = None,
               temperature: Optional[float] = None, max_tokens: Optional[int] = None,
               reasoning_effort: Optional[str] = None):
    """A LangChain chat model for this tier, built once and reused."""
    model_id = model or (tier.model if tier else config.LLM_MODEL_ID)
    parameters = _generation_parameters(tier, temperature, max_tokens, reasoning_effort)
    attempts = tier.max_attempts if tier else config.DEFAULT_MAX_ATTEMPTS
    key = (model_id, attempts) + tuple(sorted(parameters.items()))

    with _lock:
        if key in _models:
            return _models[key]

    bound = _built_model(model_id).bind(**parameters).with_retry(
        stop_after_attempt=attempts, wait_exponential_jitter=True)
    with _lock:
        _models[key] = bound
    return bound


def tool_model(tools: Sequence, tier: Optional["config.Tier"] = None,
               model: Optional[str] = None, temperature: Optional[float] = None,
               max_tokens: Optional[int] = None, reasoning_effort: Optional[str] = None):
    """The same model with tools bound, for a caller that runs a loop rather than one call."""
    model_id = model or (tier.model if tier else config.LLM_MODEL_ID)
    parameters = _generation_parameters(tier, temperature, max_tokens, reasoning_effort)
    attempts = tier.max_attempts if tier else config.DEFAULT_MAX_ATTEMPTS

    built = _built_model(model_id)
    try:
        bound = built.bind_tools(tools, **parameters)
    except (AttributeError, NotImplementedError) as exc:
        # Attempted rather than checked for. Every LangChain chat model *has* bind_tools -- the
        # base class defines it and raises NotImplementedError -- so hasattr answers yes for
        # models that cannot do it at all, and the failure then arrives from inside LangChain at
        # the first turn of the loop rather than here.
        raise ModelUnavailable(
            f"'{model_id}' cannot bind tools ({type(exc).__name__}), so an agent loop cannot run "
            f"on it. Run tools/probe_agent_support.py to see what this gateway supports.") from exc

    return bound.with_retry(stop_after_attempt=attempts, wait_exponential_jitter=True)


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
    """The two messages as a LangChain prompt, with no template variables."""
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
    """Send one system + user prompt and return the reply text."""
    record_call()
    return build_chain(system_prompt, user_message, tier=tier, model=model,
                       temperature=temperature, max_tokens=max_tokens,
                       reasoning_effort=reasoning_effort).invoke({})


def ask_llm_with_images(system_prompt: str, user_message: str, images: list,
                        temperature: float = None, max_tokens: int = None,
                        reasoning_effort: str = None, tier: "config.Tier" = None,
                        model: str = None) -> str:
    """Send a prompt with images attached, as content parts on the user message."""
    if not config.LLM_VISION:
        raise RuntimeError("Vision is disabled (LLM_VISION=off), so images cannot be sent.")

    record_call()
    return build_chain(system_prompt, user_message, images, tier=tier, model=model,
                       temperature=temperature, max_tokens=max_tokens,
                       reasoning_effort=reasoning_effort).invoke({})


_BATCH_PROMPT = None


def _batch_prompt():
    """The one reusable template every batched call is built from."""
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
    """Send several user messages under one system prompt, concurrently, and return the replies."""
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
        results.extend(chain.batch(inputs, config={"max_concurrency": concurrency},
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
        _built.clear()
