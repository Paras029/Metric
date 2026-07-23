"""Authenticated calls to the LLM gateway using a standard chat-completions request body.

One public function: ask_llm(system_prompt, user_message) -> str. The bearer token is
minted on demand and cached in memory until shortly before it expires.
"""
import base64
import hashlib
import hmac
import json
import logging
import time

import requests
import urllib3

from . import config

urllib3.disable_warnings()

_token = {"value": None, "expires_at": 0.0}
_TOKEN_TTL = 3600
_REFRESH_BUFFER = 300


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
    return response.json()["authorization_token"]


def get_token() -> str:
    """Return a cached bearer token, refreshing it shortly before expiry."""
    now = time.time()
    if _token["value"] is None or now >= _token["expires_at"]:
        _token["value"] = _mint_token()
        _token["expires_at"] = now + _TOKEN_TTL - _REFRESH_BUFFER
    return _token["value"]


def ask_llm(system_prompt: str, user_message: str,
            temperature: float = None, max_tokens: int = None,
            reasoning_effort: str = None) -> str:
    """Send one system + user prompt to the gateway and return the reply text.

    `max_tokens` and `reasoning_effort` override the configured defaults for a single call. The
    judgement passes raise both together, since reasoning is drawn from the same budget as the
    reply.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_token()}",
        "Accept": "application/json",
        "cache-control": "no-cache",
    }
    payload = {
        "model": config.LLM_MODEL_ID,
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

    response = requests.post(config.LLM_ENDPOINT, headers=headers,
                             data=json.dumps(payload), verify=False)
    if response.status_code != 200:
        raise RuntimeError(f"LLM call failed: {response.status_code} - {response.text}")

    choice = response.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        logging.getLogger(__name__).warning(
            "Reply hit the %d-token limit and was truncated. Lower the batch size first; if that "
            "does not resolve it, raise LLM_MAX_TOKENS / LLM_JUDGEMENT_MAX_TOKENS.",
            payload["max_tokens"])
    return choice["message"]["content"]
