"""The sensitivity label every workbook this tool writes carries.

A workbook written by a library arrives with no classification on it at all. Under a mandatory
labelling policy that is not a neutral state: the file is unlabelled, so the organisation's default
applies, and where that default is the most restrictive one the result is a registry nobody can
open. The fix is not to argue with the policy but to label the file correctly on the way out.

An Office file carries its label as custom document properties -- ``MSIP_Label_<id>_Name``,
``_Enabled`` and the rest -- which is a documented part of the OOXML package and something openpyxl
can write. So each workbook is stamped as it is saved.

**The label's identifier is specific to your organisation and this file does not guess it.** A
sensitivity label is a GUID minted in your own tenant; a made-up one names a label that does not
exist, which fails differently and worse than no label at all. There are two ways to supply the
real one, and the second needs nothing but a file you already have:

``sensitivity.label_id`` and ``sensitivity.name`` in tuning.yml, if you know them, or
``sensitivity.copy_from`` pointing at any workbook already labelled the way you want these
labelled -- the properties are read straight off it. Either way it is configuration, because the
right label for a registry is a decision about your data rather than about this tool.

Nothing here ever overwrites a label that is already on a workbook. Re-saving a file somebody
uploaded must not silently reclassify their document, so an existing label is left exactly as it
is and only an unlabelled workbook gets stamped.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# The prefix every label property shares. Everything between it and the field name is the label's
# own identifier, which is how a file can carry more than one and how they are told apart.
PREFIX = "MSIP_Label_"

_WARNED = set()


def _properties(workbook) -> List:
    """The custom document properties on a workbook, whatever openpyxl calls them here."""
    holder = getattr(workbook, "custom_doc_props", None)
    return list(getattr(holder, "props", []) or []) if holder is not None else []


def existing_label(workbook) -> str:
    """The id of the label already on this workbook, or "" where it carries none."""
    for prop in _properties(workbook):
        name = str(getattr(prop, "name", ""))
        if name.startswith(PREFIX) and name.endswith("_Enabled"):
            return name[len(PREFIX):-len("_Enabled")]
    return ""


def read_label(path) -> Dict[str, str]:
    """Every label property on an existing workbook, as ``{name: value}``.

    This is what makes ``copy_from`` possible: point it at a document already classified the way
    you want, and the label is lifted off it rather than transcribed by hand from a GUID somebody
    has to go and find.
    """
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(str(path))
    except Exception as exc:
        logger.warning("Could not read a sensitivity label from %s (%s).", path, exc)
        return {}

    found = {str(p.name): str(p.value) for p in _properties(workbook)
             if str(getattr(p, "name", "")).startswith(PREFIX)}
    if not found:
        logger.warning("%s carries no sensitivity label, so there is nothing to copy from it.",
                       path)
    return found


def label_properties() -> Dict[str, str]:
    """The label to stamp, as ``{property name: value}``. Empty where none is configured."""
    from ..llm import config

    if not config.SENSITIVITY_LABEL:
        return {}

    source = config.SENSITIVITY_COPY_FROM
    if source:
        return read_label(source)

    label_id = config.SENSITIVITY_LABEL_ID
    if not label_id:
        _warn_once(
            "No sensitivity label is configured, so the workbooks this writes carry none. Under a "
            "mandatory labelling policy an unlabelled file takes the organisation's default, which "
            "is usually the most restrictive one. Set sensitivity.copy_from in tuning.yml to any "
            "workbook already labelled the way you want these labelled, or sensitivity.label_id "
            "and sensitivity.name if you know them.")
        return {}

    stamped = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    values = {
        "Enabled": "true",
        "SetDate": stamped,
        "Method": config.SENSITIVITY_METHOD,
        "Name": config.SENSITIVITY_NAME,
        "ContentBits": "0",
    }
    if config.SENSITIVITY_SITE_ID:
        values["SiteId"] = config.SENSITIVITY_SITE_ID
    if config.SENSITIVITY_ACTION_ID:
        values["ActionId"] = config.SENSITIVITY_ACTION_ID
    return {f"{PREFIX}{label_id}_{field}": value for field, value in values.items()}


def apply(workbook) -> bool:
    """Stamp the configured label onto a workbook. Returns whether anything was written.

    A workbook that already carries a label is left alone. That case is a file somebody uploaded
    and this tool is re-saving, and quietly reclassifying somebody's own document is a worse thing
    to do than anything this is trying to fix.
    """
    already = existing_label(workbook)
    if already:
        logger.debug("Workbook already carries label %s; leaving it as it is.", already)
        return False

    properties = label_properties()
    if not properties:
        return False

    from openpyxl.packaging.custom import StringProperty

    holder = getattr(workbook, "custom_doc_props", None)
    if holder is None:                                     # an openpyxl too old to carry them
        _warn_once("This openpyxl cannot write custom document properties, so no sensitivity "
                   "label was applied. Upgrade to 3.1 or newer.")
        return False

    for name, value in properties.items():
        holder.append(StringProperty(name=name, value=str(value)))
    return True


def describe() -> str:
    """One line saying what will be stamped, for a run to state before it writes anything."""
    from ..llm import config

    if not config.SENSITIVITY_LABEL:
        return "Sensitivity labelling is off; workbooks are written unlabelled."
    properties = label_properties()
    if not properties:
        return "No sensitivity label configured; workbooks are written unlabelled."
    name = next((v for k, v in properties.items() if k.endswith("_Name")), "an unnamed label")
    return f"Workbooks are labelled '{name}'."


def _warn_once(message: str) -> None:
    """Said once per process. This is a configuration fact, not a per-file event, and repeating it
    for every workbook written would bury the run's own output."""
    if message not in _WARNED:
        _WARNED.add(message)
        logger.warning(message)


def forget_warnings() -> None:
    """Let the warnings be said again. For tests, and for a configuration change mid-run."""
    _WARNED.clear()
