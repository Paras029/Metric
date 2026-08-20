"""Redacting PII and other business-sensitive text out of submitted documents."""
from __future__ import annotations

import inspect
import logging
from typing import Any, List, Optional, Tuple

from metric.llm import config
from metric.phases.intake.intake.readers import Segment

logger = logging.getLogger(__name__)


class RedactionUnavailable(RuntimeError):
    """Redaction is switched on but the engine could not be loaded, or a call into it failed."""


def _load_engine():
    """Import the redaction engine's constructors, only when actually needed."""
    try:
        from ee_utils.redaction import MaskingConfig, RedactionConfig, redact_text
    except ImportError as exc:
        raise RedactionUnavailable(
            "PII_REDACTION is on but the redaction engine could not be imported "
            "(ee_utils.redaction). Install the pii-redactor package into this environment -- "
            "see the integration guide -- or set PII_REDACTION=off in .env to run without it, "
            "which is not appropriate for real submitted documents.") from exc
    return MaskingConfig, RedactionConfig, redact_text


def _accepted(cls: Any, **wanted: Any) -> dict:
    """Only the keyword arguments ``cls`` actually declares, logging what had to be dropped."""
    try:
        accepted_names = set(inspect.signature(cls).parameters)
    except (TypeError, ValueError):
        accepted_names = set(wanted)

    applied = {name: value for name, value in wanted.items()
              if value is not None and name in accepted_names}
    ignored = sorted(name for name, value in wanted.items()
                     if value is not None and name not in accepted_names)
    if ignored:
        logger.warning(
            "%s does not accept %s; the setting(s) were ignored. Check the installed "
            "pii-redactor version against the integration guide.",
            getattr(cls, "__name__", cls), ", ".join(ignored))
    return applied


def _build_config(MaskingConfig: Any, RedactionConfig: Any) -> Any:
    """The run's redaction settings, from ``.env`` -- see ``llm/config.py`` for every knob."""
    masking = MaskingConfig(**_accepted(
        MaskingConfig, replacement_text=config.PII_REDACTION_REPLACEMENT_TEXT))
    return RedactionConfig(**_accepted(
        RedactionConfig,
        mode=config.PII_REDACTION_MODE,
        masking=masking,
        sensitivity=config.PII_REDACTION_SENSITIVITY,
        exclude_entities=config.PII_REDACTION_EXCLUDE_ENTITIES or None,
        allow_list=config.PII_REDACTION_ALLOW or None,
        thresholds=config.PII_REDACTION_THRESHOLDS or None))


def redact_segments(segments: List[Segment], mapping: Optional[dict] = None,
                    force: bool = False) -> Tuple[List[Segment], Optional[dict]]:
    """Redact every segment's text, keeping its locator untouched."""
    if not (config.PII_REDACTION or force) or not segments:
        return segments, mapping

    MaskingConfig, RedactionConfig, redact_text = _load_engine()
    engine_config = _build_config(MaskingConfig, RedactionConfig)

    redacted: List[Segment] = []
    for segment in segments:
        try:
            result = redact_text(segment.text, engine_config, current_mapping=mapping)
            text, mapping = _unpack(result, mapping)
        except RedactionUnavailable:
            raise
        except Exception as exc:
            raise RedactionUnavailable(
                f"Redaction failed on a submitted passage ({exc}). Redaction is required for "
                f"this document, so nothing from it is read further until this is fixed.") from exc
        redacted.append(Segment(text=text, locator=segment.locator))
    return redacted, mapping


def _unpack(result: Any, mapping: Optional[dict]) -> Tuple[str, Optional[dict]]:
    """The redacted text and the carried mapping, whichever shape the engine returns them in."""
    if isinstance(result, str):
        return result, mapping
    if isinstance(result, tuple) and len(result) == 2:
        return str(result[0]), result[1]

    text = getattr(result, "text", None)
    if not isinstance(text, str):
        raise RedactionUnavailable(
            f"The redaction engine returned {type(result).__name__}, which carries no redacted "
            f"text this can read. Nothing from this document is read further: passing the "
            f"original through would defeat the point of having redaction on. Check the installed "
            f"pii-redactor version against the integration guide.")
    return text, getattr(result, "mapping", mapping)
