"""Redacting PII and other business-sensitive text out of submitted documents.

Every segment a document is read into passes through here before it is joined into a corpus and
sent to any model call -- see :func:`~scenario_generator.ingest.extraction.build_corpus`, which is
the one place this is wired in. Submitted packs routinely carry names, account numbers and other
material that has no reason to reach a model, however well-intentioned the call is, so this runs
before that reach exists rather than being cleaned up after.

The engine itself -- the internal ``pii-redactor`` package, built on ``ee_utils.redaction`` -- is
not part of this repository. It arrives however your team distributes internal packages, and this
module only knows how to call it once it is installed; nothing here reimplements detection or
masking. Imported lazily, the same way :mod:`scenario_generator.llm.gateway` imports SafeChain,
so every part of this tool that never touches ingestion starts without the package at all.

Off by default (``PII_REDACTION`` in ``.env``), so a fresh checkout or a machine that has not been
given the internal package yet runs unmodified. Once turned on, this is deliberately not a
best-effort extra: a segment that cannot be redacted is not sent unredacted with a warning, it
stops the run. See :class:`RedactionUnavailable`.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, List, Optional, Tuple

from ..llm import config
from .readers import Segment

logger = logging.getLogger(__name__)


class RedactionUnavailable(RuntimeError):
    """Redaction is switched on but the engine could not be loaded, or a call into it failed.

    Raised rather than logged and skipped: the whole reason ``PII_REDACTION`` is a switch rather
    than a try-and-hope is that once it is on, nothing downstream should ever have to wonder
    whether a particular passage went through it.
    """


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
    """Only the keyword arguments ``cls`` actually declares, logging what had to be dropped.

    The exact shape of ``RedactionConfig``/``MaskingConfig`` beyond ``mode`` and
    ``replacement_text`` was not confirmed against running code at the time this was written --
    ``sensitivity``, ``exclude_entities``, ``allow_list`` and ``thresholds`` are inferred from the
    integration guide's ``redact_file`` signature rather than from the config classes themselves.
    Filtering against the installed signature means a name that turns out to be wrong is a logged
    line naming exactly which setting was ignored, not a crash on the first real document.
    """
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


def redact_segments(segments: List[Segment],
                    mapping: Optional[dict] = None) -> Tuple[List[Segment], Optional[dict]]:
    """Redact every segment's text, keeping its locator untouched.

    Called after a document has been read into segments and before those segments are joined into
    a corpus, so a locator still points at the same place in the original document even though
    the text it names may no longer carry whatever was redacted out of it.

    ``mapping`` carries a substitution-mode engine's token assignments across every segment and
    every document in one ingestion run, so the same person or account is masked the same way
    everywhere in the pack rather than once per passage. It is threaded through and returned
    rather than kept here, so nothing about redaction is process-global state; it is discarded at
    the end of the run rather than written anywhere, since a mapping is itself exactly the kind of
    thing this exists to keep out of a saved file.

    A no-op, returning ``segments`` unchanged, wherever ``PII_REDACTION`` is off -- which is the
    default. See the module docstring for why that default is off rather than on.
    """
    if not config.PII_REDACTION or not segments:
        return segments, mapping

    MaskingConfig, RedactionConfig, redact_text = _load_engine()
    engine_config = _build_config(MaskingConfig, RedactionConfig)

    redacted: List[Segment] = []
    for segment in segments:
        try:
            result = redact_text(segment.text, engine_config, current_mapping=mapping)
        except Exception as exc:
            raise RedactionUnavailable(
                f"Redaction failed on a submitted passage ({exc}). PII_REDACTION is on, so "
                f"nothing from this document is read further until this is fixed.") from exc
        mapping = result.mapping
        redacted.append(Segment(text=result.text, locator=segment.locator))
    return redacted, mapping
