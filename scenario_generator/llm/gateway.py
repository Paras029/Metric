"""Authenticated calls to the LLM gateway using a standard chat-completions request body.

One public function: ask_llm(system_prompt, user_message) -> str. The bearer token is minted on
demand and cached until shortly before it expires.

Two things here exist because of how long a run can be. Ingesting a sixty-page document is
hundreds of calls over many minutes, which is long enough to outlive a token and long enough to
be throttled, and both failures arrive mid-run rather than at the start:

- The cached token's lifetime comes from what the gateway said, not from an assumption. A 401 is
  also treated as a stale token and retried once against a freshly minted one, because a token
  can be revoked or shortened server-side regardless of what it claimed on issue.
- Connection resets and 5xx replies are retried with backoff. A gateway closing the connection
  under load is not the same as a request being wrong, and giving up on the first one loses a
  passage that would have succeeded a second later.
"""
import base64
import hashlib
import hmac
import json
import logging
import threading
import time

import requests
import urllib3

from . import config

logger = logging.getLogger(__name__)

urllib3.disable_warnings()

_token = {"value": None, "expires_at": 0.0}
_token_lock = threading.Lock()

# Used only when the gateway does not say how long the token is good for.
_TOKEN_TTL = 3600
_REFRESH_BUFFER = 300

# Retries apply to failures that are about the connection or the gateway's load, never to a
# request the gateway rejected on its merits -- repeating a malformed request just wastes time.
_MAX_ATTEMPTS = 4
_BACKOFF_SECONDS = 2.0
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def _mint_token() -> str:
    """Sign an IDaaS request and exchange it for a bearer token."""
    if not config.IDAAS_APP_ID or not config.IDAAS_KEY:
        raise ValueError("IDAAS_APP_ID and IDAAS_KEY must be set in the environment.")

    secret = base64.b64decode(config.IDAAS_KEY.strip() + "=")
    timestamp = str(int(time.time() * 1000))
    message = f"{config.IDAAS_APP_ID.strip()}-2-{timestamp}"
    mac = hmac.new(secret, message.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(mac).decode("utf-8").rstrip("=")

    headers = {
        "Content-Type": "application/json",
        "X-Auth-AppID": config.IDAAS_APP_ID.strip(),
        "X-Auth-Signature": signature,
        "X-Auth-Version": "2",
        "X-Auth-Timestamp": timestamp,
        "Accept": "application/json",
    }
    response = requests.post(config.IDAAS_URL, headers=headers,
                             data=json.dumps({"scope": [config.LLM_SCOPE]}), verify=False)
    if response.status_code != 200:
        raise RuntimeError(f"IDaaS token request failed: {response.status_code} - {response.text}")

    body = response.json()
    # Prefer the lifetime the gateway states. Assuming an hour when the real token lives for
    # fifteen minutes means every call after the fifteenth minute fails with a 401 and the cache
    # never refreshes, which is a silent, mid-run death.
    lifetime = _TOKEN_TTL
    for field in ("expires_in", "expiresIn", "expires_in_seconds"):
        try:
            stated = int(body[field])
        except (KeyError, TypeError, ValueError):
            continue
        if stated > 0:
            lifetime = stated
            break
    return body["authorization_token"], lifetime


def get_token(force_refresh: bool = False) -> str:
    """Return a cached bearer token, minting a new one when it is stale or on demand."""
    with _token_lock:
        now = time.time()
        if force_refresh or _token["value"] is None or now >= _token["expires_at"]:
            value, lifetime = _mint_token()
            _token["value"] = value
            _token["expires_at"] = now + max(lifetime - _REFRESH_BUFFER, 60)
        return _token["value"]


def ask_llm(system_prompt: str, user_message: str,
            temperature: float = None, max_tokens: int = None,
            reasoning_effort: str = None, tier: "config.Tier" = None,
            model: str = None) -> str:
    """Send one system + user prompt to the gateway and return the reply text.

    Pass a ``tier`` to take its model, output cap and reasoning effort together -- that is how a
    pass says what kind of work it is doing rather than restating three numbers. The individual
    arguments still override it, one call at a time.
    """
    if tier is not None:
        model = model or tier.model
        max_tokens = tier.max_tokens if max_tokens is None else max_tokens
        reasoning_effort = reasoning_effort or tier.reasoning_effort
    payload = {
        "model": model or config.LLM_MODEL_ID,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": config.DEFAULT_TEMPERATURE if temperature is None else temperature,
        "max_tokens": config.DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
        "top_p": 0.5,
    }
    effort = reasoning_effort or config.REASONING_EFFORT
    if effort:
        payload["reasoning_effort"] = effort

    response = _post_with_retry(payload)
    choice = response.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        logging.getLogger(__name__).warning(
            "Reply hit the %d-token limit and was truncated. Lower the batch size first; if that "
            "does not resolve it, raise LLM_MAX_TOKENS / LLM_JUDGEMENT_MAX_TOKENS.",
            payload["max_tokens"])
    return choice["message"]["content"]


def ask_llm_with_images(system_prompt: str, user_message: str, images: list,
                        temperature: float = None, max_tokens: int = None,
                        reasoning_effort: str = None, tier: "config.Tier" = None,
                        model: str = None) -> str:
    """Send a prompt with images attached, as content parts on the user message.

    ``images`` is a list of (media_type, raw_bytes). They are inlined as base64 data URIs, which
    is what a chat-completions gateway accepts without a separate upload step.

    Raises if vision is switched off, so a caller that reaches here has already decided images are
    worth sending; silently dropping them would produce an answer about nothing.
    """
    if not config.LLM_VISION:
        raise RuntimeError("Vision is disabled (LLM_VISION=off), so images cannot be sent.")

    if tier is not None:
        model = model or tier.model
        max_tokens = tier.max_tokens if max_tokens is None else max_tokens
        reasoning_effort = reasoning_effort or tier.reasoning_effort

    parts = [{"type": "text", "text": user_message}]
    for media_type, raw in images:
        if len(raw) > config.MAX_IMAGE_BYTES:
            raise ValueError(
                f"an image of {len(raw) // 1000}kB exceeds the {config.MAX_IMAGE_BYTES // 1000}kB "
                f"limit; resize it or raise LLM_MAX_IMAGE_BYTES")
        encoded = base64.b64encode(raw).decode("ascii")
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:{media_type};base64,{encoded}"}})

    payload = {
        "model": model or config.LLM_MODEL_ID,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": parts},
        ],
        "temperature": config.DEFAULT_TEMPERATURE if temperature is None else temperature,
        "max_tokens": config.DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
    }
    effort = reasoning_effort or config.REASONING_EFFORT
    if effort:
        payload["reasoning_effort"] = effort

    return _post_with_retry(payload).json()["choices"][0]["message"]["content"]


def _headers(force_refresh: bool = False) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_token(force_refresh)}",
        "Accept": "application/json",
        "cache-control": "no-cache",
    }


def _post_with_retry(payload: dict) -> requests.Response:
    """Send the request, re-minting the token on a 401 and backing off on transport failures.

    A 401 partway through a run almost always means the token expired or was revoked rather than
    that the credentials are wrong, so the first one triggers a fresh token and one more attempt.
    A second 401 against a newly minted token is a real authentication problem and is raised.
    """
    last_error = None
    refreshed = False

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = requests.post(config.LLM_ENDPOINT, headers=_headers(refreshed),
                                     data=json.dumps(payload), verify=False)
        except requests.RequestException as exc:
            last_error = f"connection failed: {exc}"
        else:
            if response.status_code == 200:
                return response

            if response.status_code == 401 and not refreshed:
                logger.info("Gateway returned 401; the token has gone stale. Re-authenticating.")
                refreshed = True
                continue

            last_error = f"LLM call failed: {response.status_code} - {response.text}"
            if response.status_code not in _RETRYABLE_STATUS:
                raise RuntimeError(last_error)

        if attempt < _MAX_ATTEMPTS:
            delay = _BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.info("%s — retrying in %.0fs (attempt %d of %d).",
                        last_error, delay, attempt + 1, _MAX_ATTEMPTS)
            time.sleep(delay)

    raise RuntimeError(f"{last_error} (gave up after {_MAX_ATTEMPTS} attempts)")
