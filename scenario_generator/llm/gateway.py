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

Taking SafeChain's authentication also removed the three failures that used to be this module's
problem: a token expiring in the middle of a long ingestion, a gateway rejecting a payload shaped
for a different provider, and a 401 that meant "stale" rather than "wrong". The code for those is
deleted rather than kept as a fallback, because two paths to the same call is how they drift.

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
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import config

logger = logging.getLogger(__name__)

# Retries cover a gateway that is busy or briefly unreachable. Anything the model rejects on its
# merits is raised: repeating a malformed request only wastes the time it takes to fail again.
MAX_ATTEMPTS = 4

_models: Dict[Tuple, Any] = {}
_lock = threading.Lock()


class ModelUnavailable(RuntimeError):
    """SafeChain could not be loaded, or did not return a usable LangChain model."""


def _load_safechain():
    """Import SafeChain's ``model`` factory, with the environment prepared first.

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

    try:
        from safechain.core_model import model
    except ImportError as exc:                             # pragma: no cover - environment issue
        raise ModelUnavailable(
            f"SafeChain could not be imported ({exc}). Install it from the internal index and "
            f"check the interpreter is Python 3.12.") from exc
    return model


def _generation_parameters(tier: Optional["config.Tier"], temperature: Optional[float],
                           max_tokens: Optional[int],
                           reasoning_effort: Optional[str]) -> Dict[str, Any]:
    """What the model should generate with. Bound to the model rather than passed per call."""
    parameters: Dict[str, Any] = {
        "temperature": config.DEFAULT_TEMPERATURE if temperature is None else temperature,
        "max_tokens": (max_tokens if max_tokens is not None
                       else tier.max_tokens if tier else config.DEFAULT_MAX_TOKENS),
    }
    effort = reasoning_effort or (tier.reasoning_effort if tier else config.REASONING_EFFORT)
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
    key = (model_id,) + tuple(sorted(parameters.items()))

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
            stop_after_attempt=MAX_ATTEMPTS, wait_exponential_jitter=True)
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
    return build_chain(system_prompt, user_message, tier=tier, model=model,
                       temperature=temperature, max_tokens=max_tokens,
                       reasoning_effort=reasoning_effort).invoke({})


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

    return build_chain(system_prompt, user_message, images, tier=tier, model=model,
                       temperature=temperature, max_tokens=max_tokens,
                       reasoning_effort=reasoning_effort).invoke({})


def reset_models() -> None:
    """Drop the built models, so a configuration change takes effect without a restart."""
    with _lock:
        _models.clear()
