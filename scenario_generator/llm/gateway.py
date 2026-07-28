"""Model calls, built as LangChain chains and run through SafeChain.

SafeChain owns authentication, token refresh and the request body each model expects, and hands
back a LangChain chat model. That removes the three things that used to fail mid-run and had to
be handled here: a token expiring in the middle of a long ingestion, a gateway rejecting a payload
shaped for a different provider, and a 401 that meant "stale" rather than "wrong". None of that is
this module's business any more, and the code for it is gone rather than kept as a fallback --
two paths to the same call is how they drift apart.

What is left is small: turn a system prompt and a user message into a chain, and invoke it.

    prompt | model  ->  invoke  ->  text

**Prompts are passed as messages rather than as templates, deliberately.** LangChain's tuple form
-- ``("system", text)`` -- runs the text through an f-string parser, and every prompt in this
package ends with a JSON output specification. ``{"resolved": [{"question": "..."}]}`` is not a
template variable, and handing it to that parser raises before a single call is made. Substitution
happens in :mod:`scenario_generator.llm.prompt_loader`, which uses a doubled-brace form precisely
so that literal braces survive, and what arrives here is finished text.
"""
from __future__ import annotations

import base64
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .calling import accepts

logger = logging.getLogger(__name__)

# Transient failures are the gateway being busy rather than the request being wrong, so they are
# retried with backoff. Anything the model rejects on its merits is raised: repeating a malformed
# request only wastes the time it takes to fail again.
MAX_ATTEMPTS = 4

_models: Dict[Tuple, Any] = {}
_lock = threading.Lock()


class ModelUnavailable(RuntimeError):
    """SafeChain could not be loaded or could not build the requested model."""


def _load_safechain():
    """Import SafeChain, with the environment prepared first.

    Imported here rather than at module load so that the command line, the tests and the interface
    all start without it. Only a pass that actually calls a model needs it, which keeps a missing
    or unapproved package from taking down the whole tool.
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
    """What the model should be built with. Set at construction, not per call."""
    parameters: Dict[str, Any] = {
        "temperature": config.DEFAULT_TEMPERATURE if temperature is None else temperature,
        "max_tokens": (max_tokens if max_tokens is not None
                       else tier.max_tokens if tier else config.DEFAULT_MAX_TOKENS),
    }
    effort = reasoning_effort or (tier.reasoning_effort if tier else config.REASONING_EFFORT)
    if effort:
        parameters["reasoning_effort"] = effort
    return parameters


def _construct(build, model_id: str, parameters: Dict[str, Any]):
    """Ask SafeChain for a model, however this version of it takes its parameters.

    The generation parameters go in at construction rather than at call time. Which argument
    carries them has varied between SafeChain releases, so the signature is asked rather than
    assumed, and ``bind`` -- LangChain's own way of fixing call arguments on a runnable -- is the
    last resort. Guessing wrong here would be silent: the model would build, run, and quietly
    ignore the token cap.
    """
    if accepts(build, "model_kwargs"):
        return build(model_id, model_kwargs=parameters)
    for name in ("parameters", "params", "generation_config", "config"):
        if accepts(build, name):
            return build(model_id, **{name: parameters})

    built = build(model_id)
    if hasattr(built, "bind"):
        return built.bind(**parameters)

    logger.warning("SafeChain's model() took no generation parameters, so %s is running on its "
                   "configured defaults rather than this tier's.", model_id)
    return built


def chat_model(tier: Optional["config.Tier"] = None, model: Optional[str] = None,
               temperature: Optional[float] = None, max_tokens: Optional[int] = None,
               reasoning_effort: Optional[str] = None):
    """A LangChain chat model for this tier, built once and reused.

    Construction reads a config file and sets up credentials, so doing it per call would repeat
    that work on every batch of every pass. The cache is keyed on everything that can change the
    model, so an override still gets its own.
    """
    model_id = model or (tier.model if tier else config.LLM_MODEL_ID)
    parameters = _generation_parameters(tier, temperature, max_tokens, reasoning_effort)
    key = (model_id,) + tuple(sorted(parameters.items()))

    with _lock:
        if key not in _models:
            build = _load_safechain()
            try:
                built = _construct(build, model_id, parameters)
            except ModelUnavailable:
                raise
            except Exception as exc:
                raise ModelUnavailable(
                    f"SafeChain could not build '{model_id}' ({exc}). The name has to match one "
                    f"declared in the config.yml at CONFIG_PATH.") from exc
            _models[key] = built.with_retry(stop_after_attempt=MAX_ATTEMPTS,
                                            wait_exponential_jitter=True) \
                if hasattr(built, "with_retry") else built
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


def _text(reply: Any) -> str:
    """The reply as a string, whichever shape the model returned it in.

    A chat model returns a message; some return content as a list of parts. Both are unwrapped
    here so no caller has to know which it got.
    """
    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part)
                       for part in content)
    return content if isinstance(content, str) else str(content)


def ask_llm(system_prompt: str, user_message: str,
            temperature: float = None, max_tokens: int = None,
            reasoning_effort: str = None, tier: "config.Tier" = None,
            model: str = None) -> str:
    """Send one system + user prompt and return the reply text.

    Pass a ``tier`` to take its model, output cap and reasoning effort together -- that is how a
    pass says what kind of work it is doing rather than restating three numbers. The individual
    arguments still override it, one call at a time.
    """
    chain = build_prompt(system_prompt, user_message) | chat_model(
        tier=tier, model=model, temperature=temperature, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort)
    return _text(chain.invoke({}))


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

    chain = build_prompt(system_prompt, user_message, images) | chat_model(
        tier=tier, model=model, temperature=temperature, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort)
    return _text(chain.invoke({}))


def reset_models() -> None:
    """Drop the built models, so a configuration change takes effect without a restart."""
    with _lock:
        _models.clear()
