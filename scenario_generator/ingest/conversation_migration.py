"""Migrating somebody else's export onto the shape the coverage stage reads.

The tool asks for its own data template back, and where that is what arrives nothing here runs --
see :func:`conversations._from_returned_template`. What this exists for is everything else: a
logging export, a QA spreadsheet, a hand-kept tracker, in whatever columns that team's system
happened to produce.

There is a deterministic reader for those too, and it recognises the common shapes by their
headings. It is kept, but as the safety net rather than as the first answer, because heading
matching fails in exactly the way that hurts most: silently and plausibly. A column headed
`Utterance` is found; one headed `cust_msg_body_txt` is not, and the reader falls back to "widest
column carries the words", which will happily pick the agent's reasoning trace and label it as the
user. Nothing about the result looks wrong. The coverage figure is simply built on the wrong
column, and the person defending that figure has no way to tell.

So the shape is *asked about* instead. One call, shown the header and a handful of rows, naming
which column is which. One call for the whole file however large it is -- the model reads the
header, not the transcripts, and every row is then processed in code from what it said. That is
also why this is worth doing at all: the alternative is not a cheaper call, it is a wrong column.

The reply is checked against the header before it is used. A model that names a column the file
does not have is more likely to have misread the sample than to have found something, and falling
back to the deterministic reader is better than reading a column that is not there.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from ..llm import config, prompt_loader
from ..llm.calling import call
from ..llm.gateway import ask_llm
from ..utils import parse_json_object

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "coverage.system"
_MIGRATE_PROMPT = "coverage.migrate"

# How much of the file the call is shown. Enough rows to tell a repeated conversation id from a
# unique one and a sentence column from a code column; not so many that a wide export blows the
# prompt. Cells are cut because one row of a transcript export can be a whole conversation.
SAMPLE_ROWS = 8
SAMPLE_CELL_CHARS = 220

TURN_PER_ROW = "turn_per_row"
CONVERSATION_PER_ROW = "conversation_per_row"

_COLUMNS = ("conversation_id", "speaker", "text", "user_text", "agent_text", "scenario_id",
            "group")


def sample(rows: List[List[str]]) -> str:
    """The header and the first few rows, as a table a prompt can carry."""
    lines = []
    for index, row in enumerate(rows[:SAMPLE_ROWS + 1]):
        cells = [str(cell if cell is not None else "").strip()[:SAMPLE_CELL_CHARS] for cell in row]
        label = "header" if index == 0 else f"row {index}"
        lines.append(f"{label}: " + " | ".join(cells))
    return "\n".join(lines) or "(the file has no rows)"


def migrate(rows: List[List[str]], origin: str,
            complete: Optional[Callable[..., str]] = None) -> Optional[Dict[str, object]]:
    """Ask which column is which. Returns a mapping onto the reader's shape, or ``None``.

    ``None`` means "use the deterministic reader": the call did not come back, the reply did not
    parse, or it named columns the file does not have. Every one of those is a reason to fall back
    rather than to fail, because a heading-matched reading is a worse answer than an asked-for one
    and a much better answer than none.
    """
    if not rows:
        return None

    header = [str(cell if cell is not None else "").strip() for cell in rows[0]]
    complete = complete or ask_llm
    user = prompt_loader.render(_MIGRATE_PROMPT, origin=origin, sample=sample(rows))

    try:
        reply = call(complete, prompt_loader.load(_SYSTEM_PROMPT), user,
                     tier=config.stage_tier("COVERAGE_MIGRATE", config.JUDGEMENT))
        answer = parse_json_object(reply)
    except Exception as exc:
        logger.warning("Could not work out the layout of %s, so it is read by its headings "
                       "instead: %s", origin, exc)
        return None

    mapping = _settle(answer, header, origin)
    if mapping is None:
        return None

    logger.info("%s read as %s. %s", origin, mapping["layout"].replace("_", " "),
                mapping.get("note") or "")
    return mapping


def _settle(answer: dict, header: List[str], origin: str) -> Optional[Dict[str, object]]:
    """The reply as column indices, or ``None`` if it does not describe this file.

    Matched case-insensitively and on trimmed text: a model quoting a heading back with different
    capitalisation has still identified the column, and refusing that would throw away a correct
    answer over whitespace.
    """
    lowered = {cell.strip().lower(): index for index, cell in enumerate(header)}
    columns: Dict[str, int] = {}
    for key in _COLUMNS:
        named = str(answer.get(key) or "").strip().lower()
        if not named:
            columns[key] = -1
            continue
        if named not in lowered:
            logger.warning("The layout given for %s names a column %r that is not in its header, "
                           "so it is read by its headings instead.", origin, answer.get(key))
            return None
        columns[key] = lowered[named]

    layout = str(answer.get("layout") or "").strip()
    if layout not in (TURN_PER_ROW, CONVERSATION_PER_ROW):
        logger.warning("The layout given for %s (%r) is not one this reads, so it is read by its "
                       "headings instead.", origin, layout)
        return None

    # Whatever else it said, something has to carry the words, or there is nothing to read.
    if columns["text"] < 0 and columns["user_text"] < 0 and columns["agent_text"] < 0:
        logger.warning("The layout given for %s names no column carrying the conversation, so it "
                       "is read by its headings instead.", origin)
        return None

    return {"layout": layout, "columns": columns,
            "speakers": _speaker_values(answer),
            "note": str(answer.get("note") or "").strip()}


def _speaker_values(answer: dict) -> Dict[str, str]:
    """The file's own spelling of each side, as {value: "user"|"agent"}.

    Nothing is required here and nothing is checked against the file: an export that says `C` and
    `A`, or `1` and `2`, is the normal case rather than the exception, and a code left untranslated
    files every utterance under a speaker that means nothing downstream. Where the model offers
    nothing, the existing word list does what it always did.
    """
    from .conversations import AGENT, USER

    values: Dict[str, str] = {}
    for key, side in (("user_values", USER), ("agent_values", AGENT)):
        raw = answer.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        for value in raw:
            text = str(value).strip().lower()
            if text:
                values[text] = side
    return values
