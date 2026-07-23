"""Robust parsing of a JSON object out of an LLM reply.

Models routinely wrap JSON in markdown fences, add surrounding prose, or (rarely) truncate a
reply mid-object. `parse_json_object` tolerates all three: it strips fences, parses leniently
(literal newlines inside string values are allowed), and if the whole blob still won't parse,
salvages whichever individual `"id": {...}` objects are intact rather than losing the batch.
"""
from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_OBJECT_KEY = re.compile(r'"([^"\n]+)"\s*:\s*\{')


def _strip_fences(text: str) -> str:
    """Return the JSON body from a reply, unwrapping a ```json ... ``` fence if present."""
    fenced = _FENCE.search(text)
    return (fenced.group(1) if fenced else text).strip()


def _balanced_object(text: str, open_index: int):
    """Substring for the brace-balanced object starting at text[open_index] == '{'."""
    depth, in_string, escaped = 0, False, False
    for i in range(open_index, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_index:i + 1]
    return None


def _salvage_objects(snippet: str) -> dict:
    """Best effort when a batch won't parse whole: parse each '"id": { ... }' entry alone."""
    result = {}
    for match in _OBJECT_KEY.finditer(snippet):
        obj = _balanced_object(snippet, match.end() - 1)
        if obj is None:
            continue
        try:
            result[match.group(1)] = json.loads(obj, strict=False)
        except json.JSONDecodeError:
            continue
    if not result:
        raise ValueError("could not salvage any JSON objects from the reply")
    return result


def parse_json_object(text: str) -> dict:
    """Parse the model's JSON reply, tolerating fences, prose, and newlines inside strings."""
    body = _strip_fences(text)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in model reply")
    snippet = body[start:end + 1]
    try:
        return json.loads(snippet, strict=False)   # strict=False allows literal newlines in strings
    except json.JSONDecodeError:
        return _salvage_objects(snippet)
