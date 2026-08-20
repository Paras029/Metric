"""Reading the conversations a model owner actually ran, out of whatever they sent."""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from metric.phases.coverage.coverage import migration as conversation_migration

logger = logging.getLogger(__name__)

# Headings, in the words teams actually use. Matched case-insensitively, longest first, so
# "conversation id" is not claimed by "id".
_CONVERSATION_ID = ("conversation id", "conversation_id", "conv id", "convo id", "session id",
                    "session_id", "chat id", "transcript id", "dialogue id", "call id",
                    "interaction id", "conversation", "session", "id")
# No turn-number headings, deliberately: rows are taken in file order -- see _turn_per_row.
_SPEAKER = ("speaker", "role", "actor", "author", "participant", "from", "direction", "sender",
            "side")
_TEXT = ("message text", "utterance", "message", "text", "content", "transcript", "dialogue",
         "conversation text", "body", "said")
# What the team may already have grouped by. Read, never trusted -- see the module docstring.
_GROUP = ("scenario id", "scenario name", "scenario", "test case id", "test case", "use case",
          "intent", "category", "group", "label", "bucket", "tag")

# "User: ..." / "[AGENT] ..." / "Customer -" at the start of a line.
_SPEAKER_PREFIX = re.compile(
    r"^\s*[\[\(<]?\s*(user|customer|caller|member|human|client|agent|bot|assistant|system|ivr|"
    r"advisor|rep|representative)\s*[\]\)>]?\s*[:\-–]\s*(.*)$", re.I)

# Which side of the conversation a label means. Anything unrecognised is kept verbatim: a
# transcript with a speaker this does not know is still a transcript.
_USER_WORDS = {"user", "customer", "caller", "member", "human", "client", "consumer", "cardmember"}
_AGENT_WORDS = {"agent", "bot", "assistant", "system", "ivr", "advisor", "rep", "representative",
                "virtual agent", "va"}

USER, AGENT = "user", "agent"

# Below this a "conversation" is a stray cell rather than an exchange worth mapping.
MIN_TRANSCRIPT_CHARS = 20


class UnreadableConversations(Exception):
    """The submitted file could not be read as conversations, with a reason worth showing."""


@dataclass(frozen=True)
class Turn:
    """One utterance. ``speaker`` is normalised to user/agent where it is recognisable."""

    speaker: str
    text: str

    def __str__(self) -> str:
        return f"{self.speaker.title()}: {self.text}"


@dataclass
class Conversation:
    """One exchange the model owner ran, and whatever they filed it under."""

    id: str
    turns: List[Turn] = field(default_factory=list)
    group: str = ""
    """The team's own scenario label for this conversation, where they supplied one.

    Never used to decide what the conversation covers -- that is read from the transcript. It is
    kept so their grouping can be compared against ours, which is a finding in its own right.
    """

    scenario_id: str = ""
    """Which of *our* scenarios this conversation was run against, where the file says so.

    Distinct from ``group`` in the only way that matters: a group is what the team called their
    own test and is evidence to be weighed, where this is an id from the data template we issued,
    filled in on the row the team was asked to fill in. That is not a claim to assess -- it is the
    answer to the question the mapping call exists to ask, so a conversation carrying one skips
    that call entirely.
    """

    @property
    def transcript(self) -> str:
        return "\n".join(str(turn) for turn in self.turns)

    @property
    def user_turns(self) -> List[Turn]:
        return [t for t in self.turns if t.speaker == USER]

    def __bool__(self) -> bool:
        return len(self.transcript.strip()) >= MIN_TRANSCRIPT_CHARS


def _speaker(raw: str) -> str:
    """A speaker label reduced to the side it is on, or kept as written."""
    word = str(raw or "").strip().lower().strip(":-–[]()<>")
    if word in _USER_WORDS:
        return USER
    if word in _AGENT_WORDS:
        return AGENT
    return word or USER


def _split_prefixed(text: str) -> List[Turn]:
    """A block of text with "User:"/"Agent:" prefixes, as turns."""
    turns: List[Turn] = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        match = _SPEAKER_PREFIX.match(line)
        if match:
            turns.append(Turn(_speaker(match.group(1)), match.group(2).strip()))
        elif turns:
            turns[-1] = Turn(turns[-1].speaker, f"{turns[-1].text} {line.strip()}".strip())
        else:
            turns.append(Turn(USER, line.strip()))
    return turns


def _find(header: List[str], candidates: Tuple[str, ...]) -> int:
    """The column matching one of ``candidates``, longest candidate first, or -1."""
    lowered = [c.strip().lower() for c in header]
    for candidate in sorted(candidates, key=len, reverse=True):
        for index, cell in enumerate(lowered):
            if candidate == cell:
                return index
    for candidate in sorted(candidates, key=len, reverse=True):
        for index, cell in enumerate(lowered):
            if candidate in cell:
                return index
    return -1


def _header_index(rows: List[List[str]]) -> int:
    """Which of the first few rows is the header. Spreadsheets carry titles and blank spacers."""
    words = _CONVERSATION_ID + _SPEAKER + _TEXT + _GROUP

    def score(row: List[str]) -> int:
        text = " ".join(row).lower()
        return sum(1 for word in words if word in text)

    return max(range(min(5, len(rows))), key=lambda i: score(rows[i]))


def _cell(row: List[str], index: int) -> str:
    return row[index].strip() if 0 <= index < len(row) else ""


# --------------------------------------------------------------------------- the template back
#
# The workbook this tool issues is a template: two reference sheets saying what to run, and a
# Variation_Log the team fills in one row per scenario, variation and turn. When it comes back
# filled in, it is the best submission there is -- every conversation already carries the id of
# the scenario it was run against, which is the exact thing the mapping call exists to work out.
_LOG_SHEET = "Variation_Log"
_LOG_COLUMNS = ("SC ID", "Variation", "Turn", "User Input (Actual)", "Agent Response")

# Sheet names an older issued template used, so a pack that went out before the rename still reads.
_LOG_SHEET_NAMES = (_LOG_SHEET, "Run_Log")


def _is_returned_template(header: List[str]) -> bool:
    """Whether this sheet's header is the log we issued, however the team reordered it."""
    present = {str(cell or "").strip().lower() for cell in header}
    required = {"sc id", "user input (actual)", "agent response"}
    return required <= present


def _from_returned_template(rows: List[List[str]], origin: str) -> Tuple[List[Conversation], str]:
    """The issued log, filled in: one conversation per (scenario, variation), turns in file order."""
    # Normalised here rather than relying on the caller: this is reached both from _from_rows,
    # which has already stringified, and straight off the workbook, where Variation and Turn are
    # still integers.
    rows = [[str(cell if cell is not None else "").strip() for cell in row] for row in rows]
    lowered = [cell.lower() for cell in rows[0]]

    def column(name: str) -> int:
        return lowered.index(name.lower()) if name.lower() in lowered else -1

    id_col = column("SC ID")
    variation_col = column("Variation")
    if variation_col < 0:
        variation_col = column("Run")                      # an older issued template
    user_col = column("User Input (Actual)")
    agent_col = column("Agent Response")

    found: Dict[str, Conversation] = {}
    for row in rows[1:]:
        scenario_id = _cell(row, id_col)
        if not scenario_id:
            continue
        variation = _cell(row, variation_col) or "1"
        key = f"{scenario_id}#{variation}"
        conversation = found.setdefault(
            key, Conversation(id=key, scenario_id=scenario_id))
        for column_index, speaker in ((user_col, USER), (agent_col, AGENT)):
            text = _cell(row, column_index)
            if text:
                conversation.turns.append(Turn(speaker, text))

    kept = [c for c in found.values() if c]
    if not kept:
        raise UnreadableConversations(
            f"{origin} is the data template we issued, but its {_LOG_SHEET} sheet has no filled-in "
            f"turns. Ask for the same file back with the User Input and Agent Response columns "
            f"completed.")
    return kept, (f"{origin}, the data template returned filled in — {len(kept)} conversation(s) "
                  f"already labelled with the scenario each was run against")


def _from_rows(rows: List[List[str]], origin: str) -> Tuple[List[Conversation], str]:
    """Turn a table into conversations, working out which of the two shapes it is."""
    rows = [[str(cell or "").strip() for cell in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        raise UnreadableConversations(f"{origin} has no rows.")

    head = _header_index(rows)
    header, body = rows[head], rows[head + 1:]
    if not body:
        raise UnreadableConversations(f"{origin} has a header but no data rows.")

    # Our own template first. Every heuristic below is about recognising somebody else's format;
    # this one is about recognising the format we asked for, and it is the only one that can say
    # what a conversation was run against rather than infer it.
    if _is_returned_template(header):
        return _from_returned_template([header] + body, origin)

    id_col = _find(header, _CONVERSATION_ID)
    speaker_col = _find(header, _SPEAKER)
    text_col = _find(header, _TEXT)
    group_col = _find(header, _GROUP)
    # A group heading and an id heading can match the same column ("scenario id" is both an id
    # word and a group word); the id wins, since without it nothing can be grouped into
    # conversations at all.
    if group_col == id_col:
        group_col = -1

    if text_col < 0:                                       # widest column carries the words
        widths = [sum(len(_cell(row, i)) for row in body)
                  for i in range(max((len(r) for r in body), default=0))]
        if not widths:
            raise UnreadableConversations(f"{origin} has a header but no readable text.")
        text_col = widths.index(max(widths))

    # Turn per row is the shape where a speaker column exists *and* an id repeats down the sheet.
    ids = [_cell(row, id_col) for row in body] if id_col >= 0 else []
    repeats = len(ids) > len(set(i for i in ids if i))
    if speaker_col >= 0 and repeats:
        return _turn_per_row(body, origin, id_col, speaker_col, text_col, group_col)
    return _conversation_per_row(body, origin, id_col, text_col, group_col)


def _turn_per_row(body: List[List[str]], origin: str, id_col: int, speaker_col: int,
                  text_col: int, group_col: int) -> Tuple[List[Conversation], str]:
    """One row per utterance, grouped by the conversation id repeated down the sheet."""
    found: Dict[str, Conversation] = {}
    for row in body:
        identifier = _cell(row, id_col) or f"C-{len(found) + 1:03d}"
        text = _cell(row, text_col)
        if not text:
            continue
        conversation = found.setdefault(identifier, Conversation(id=identifier))
        conversation.turns.append(Turn(_speaker(_cell(row, speaker_col)), text))
        if group_col >= 0 and not conversation.group:
            conversation.group = _cell(row, group_col)

    kept = [c for c in found.values() if c]
    if not kept:
        raise UnreadableConversations(
            f"{origin} looked like one row per turn, but no conversation reached "
            f"{MIN_TRANSCRIPT_CHARS} characters.")
    return kept, f"{origin}, one row per turn grouped by conversation id"


def _conversation_per_row(body: List[List[str]], origin: str, id_col: int, text_col: int,
                          group_col: int) -> Tuple[List[Conversation], str]:
    """One row per conversation, with the whole transcript in a single cell."""
    conversations = []
    for number, row in enumerate(body, start=1):
        text = _cell(row, text_col)
        if len(text) < MIN_TRANSCRIPT_CHARS:
            continue
        identifier = _cell(row, id_col) or f"C-{number:03d}"
        conversation = Conversation(id=identifier, turns=_split_prefixed(text),
                                    group=_cell(row, group_col) if group_col >= 0 else "")
        if conversation:
            conversations.append(conversation)

    if not conversations:
        raise UnreadableConversations(
            f"{origin} was read but no row held a transcript of at least "
            f"{MIN_TRANSCRIPT_CHARS} characters.")
    return conversations, f"{origin}, one row per conversation"


def _from_workbook(path: Path, complete=None) -> Tuple[List[Conversation], str]:
    from openpyxl import load_workbook

    try:
        book = load_workbook(str(path), data_only=True, read_only=True)
    except Exception as exc:
        # A .xlsx file is a zip archive; anything else under that extension fails here with a
        # message that names the container format rather than the fix. The likely causes are all
        # ones the sender can act on: an older .xls saved under the wrong extension, a download
        # that did not finish, or a password-protected file -- openpyxl cannot open any of those,
        # and the raw error ("File is not a zip file") does not say so.
        raise UnreadableConversations(
            f"{path.name} could not be opened as an Excel workbook ({exc}). This usually means "
            f"the file is not really .xlsx underneath -- an older .xls saved with the wrong "
            f"extension, a download that did not finish, or a password-protected file. Re-save an "
            f"unprotected copy from Excel (File > Save As > Excel Workbook), or send it as .csv, "
            f"which this reads just as well.") from exc

    try:
        # Our own log first, by name and by header. Picking the sheet with the most rows is the
        # right rule among somebody else's sheets and the wrong one here: the template ships an
        # Instructions sheet of prose that reads perfectly well as conversations, and on a returned
        # template that sheet won -- so the file the tool asked for was read as its own covering
        # note.
        for name in book.sheetnames:
            if name not in _LOG_SHEET_NAMES:
                continue
            rows = [list(r) for r in book[name].iter_rows(values_only=True)]
            if rows and _is_returned_template([str(c or "") for c in rows[0]]):
                return _from_returned_template(rows, f"sheet '{name}'")

        # Which sheet, before which columns. Asking about every sheet in a workbook would spend a
        # call on the covering note and the summary tab as readily as on the transcripts, so the
        # sheet is chosen by headings first and only the winner is asked about.
        best, best_error, best_rows = None, None, None
        for name in book.sheetnames:
            rows = [list(r) for r in book[name].iter_rows(values_only=True)]
            try:
                conversations, how = _from_rows(rows, f"sheet '{name}'")
            except UnreadableConversations as exc:
                best_error = best_error or exc
                continue
            if best is None or len(conversations) > len(best[0]):
                best, best_rows = (conversations, how), (rows, f"sheet '{name}'")
    finally:
        book.close()

    if best is None:
        raise UnreadableConversations(
            str(best_error) if best_error else "no sheet held anything readable as conversations.")
    if complete is not None and best_rows is not None:
        return _read_table(best_rows[0], best_rows[1], complete)
    return best


def _from_csv(path: Path, complete=None) -> Tuple[List[Conversation], str]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        head = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(head, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [list(row) for row in csv.reader(handle, dialect)]
    return _read_table(rows, path.name, complete)


def _read_table(rows: List[List[str]], origin: str, complete=None
                ) -> Tuple[List[Conversation], str]:
    """One sheet or one CSV: our template, then an asked-for mapping, then headings."""
    normalised = [[str(cell if cell is not None else "").strip() for cell in row] for row in rows]
    normalised = [row for row in normalised if any(row)]
    if normalised and _is_returned_template(normalised[_header_index(normalised)]):
        head = _header_index(normalised)
        return _from_returned_template(normalised[head:], origin)

    if complete is not None and normalised:
        head = _header_index(normalised)
        mapping = conversation_migration.migrate(normalised[head:], origin, complete)
        if mapping is not None:
            try:
                return _from_mapping(normalised[head:], origin, mapping)
            except UnreadableConversations as exc:
                logger.warning("The columns worked out for %s did not yield conversations (%s); "
                               "reading it by its headings instead.", origin, exc)

    return _from_rows(rows, origin)


def _from_text(text: str, origin: str) -> Tuple[List[Conversation], str]:
    """A document with no table: conversations separated by headings or blank lines."""
    blocks: List[Tuple[str, List[str]]] = []
    label, current = "", []
    for line in text.splitlines():
        stripped = line.strip()
        heading = stripped.lstrip("#").strip() if stripped.startswith("#") else ""
        blank_break = not stripped and current and len(current) > 1
        if heading or blank_break:
            if current:
                blocks.append((label, current))
            label, current = (heading or ""), []
            continue
        if stripped:
            current.append(line)
    if current:
        blocks.append((label, current))

    conversations = []
    for number, (heading, lines) in enumerate(blocks, start=1):
        body = "\n".join(lines)
        # A prose file carries no column heading to say "this is a transcript", so the speaker
        # prefixes are the only evidence that it is one. Without them this would read any
        # paragraph as a one-turn conversation, and a team whose format was not understood would
        # be reported as having tested something they did not.
        if not any(_SPEAKER_PREFIX.match(line) for line in lines):
            continue
        conversation = Conversation(id=heading or f"C-{number:03d}", turns=_split_prefixed(body))
        if conversation:
            conversations.append(conversation)

    if not conversations:
        raise UnreadableConversations(
            f"{origin} has no text that reads as a conversation. Each one needs its turns marked, "
            f"e.g. 'User: ...' and 'Agent: ...'.")
    return conversations, f"{origin}, conversations separated by headings or blank lines"


def _from_docx(path: Path) -> Tuple[List[Conversation], str]:
    from docx import Document

    try:
        document = Document(str(path))
    except Exception as exc:
        raise UnreadableConversations(f"could not open the Word document ({exc})") from exc

    for table in document.tables:
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        try:
            return _from_rows(rows, "the first table")
        except UnreadableConversations:
            continue
    return _from_text("\n".join(p.text for p in document.paragraphs), path.name)


def _from_pdf(path: Path) -> Tuple[List[Conversation], str]:
    """A PDF of transcripts, read as prose. Tables in a PDF are not tables by the time they get
    here -- the extracted text has lost the cells -- so the speaker prefixes are all there is to
    go on, which is the same thing :func:`_from_text` already works from."""
    from pypdf import PdfReader

    try:
        pages = PdfReader(str(path)).pages
    except Exception as exc:
        raise UnreadableConversations(f"could not open the PDF ({exc})") from exc
    return _from_text("\n".join((page.extract_text() or "") for page in pages), path.name)


def redact_conversations(conversations: List[Conversation], force: bool = False) -> List[Conversation]:
    """Every utterance through the redactor, in one pass over the whole submission."""
    from metric.phases.intake.intake.readers import Segment
    from metric.phases.intake.intake.redaction import redact_segments

    index = [(c, position) for c in conversations for position, _ in enumerate(c.turns)]
    segments = [Segment(text=c.turns[position].text, locator=f"{c.id}:{position}")
                for c, position in index]
    if not segments:
        return conversations

    cleaned, _ = redact_segments(segments, force=force)
    for (conversation, position), segment in zip(index, cleaned):
        conversation.turns[position] = Turn(conversation.turns[position].speaker, segment.text)
    return conversations


def _from_mapping(rows: List[List[str]], origin: str, mapping: dict
                  ) -> Tuple[List[Conversation], str]:
    """Read every row using the column mapping a migration call settled."""
    columns = mapping["columns"]
    # The file's own spelling of each side, where the migration named it. Checked before the word
    # list, because a one-letter code is exactly what the word list cannot know.
    spellings = mapping.get("speakers") or {}
    body = [[str(cell if cell is not None else "").strip() for cell in row] for row in rows[1:]]
    body = [row for row in body if any(row)]
    if not body:
        raise UnreadableConversations(f"{origin} has a header but no data rows.")

    found: Dict[str, Conversation] = {}
    order: List[str] = []

    def conversation_for(row: List[str], fallback: int) -> Conversation:
        identifier = _cell(row, columns["conversation_id"]) or f"C-{fallback:03d}"
        if identifier not in found:
            found[identifier] = Conversation(
                id=identifier,
                group=_cell(row, columns["group"]),
                scenario_id=_cell(row, columns["scenario_id"]))
            order.append(identifier)
        return found[identifier]

    for index, row in enumerate(body, start=1):
        if mapping["layout"] == conversation_migration.CONVERSATION_PER_ROW:
            text = _cell(row, columns["text"])
            if not text:
                continue
            conversation = conversation_for(row, index)
            conversation.turns.extend(_split_prefixed(text))
            continue

        # One row per turn, in either of its two forms: a speaker column, or the two sides in
        # two columns of the same row -- which is the shape our own template uses.
        conversation = conversation_for(row, index)
        if columns["speaker"] >= 0 and columns["text"] >= 0:
            text = _cell(row, columns["text"])
            if text:
                raw = _cell(row, columns["speaker"])
                conversation.turns.append(
                    Turn(spellings.get(raw.strip().lower()) or _speaker(raw), text))
        for column, speaker in ((columns["user_text"], USER), (columns["agent_text"], AGENT)):
            text = _cell(row, column) if column >= 0 else ""
            if text:
                conversation.turns.append(Turn(speaker, text))

    kept = [found[identifier] for identifier in order if found[identifier]]
    if not kept:
        raise UnreadableConversations(
            f"{origin} was read with the columns worked out for it, but no conversation reached "
            f"{MIN_TRANSCRIPT_CHARS} characters.")
    note = f" {mapping['note']}" if mapping.get("note") else ""
    return kept, (f"{origin}, {mapping['layout'].replace('_', ' ')}, with its columns worked out "
                  f"by a first pass over the file.{note}")


def read_conversations(path: Path, complete: Optional[Callable[..., str]] = None,
                       migrate: bool = True) -> Tuple[List[Conversation], str]:
    """Read submitted conversations. Returns them and a note of how the file was read."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xlsm"):
        conversations, how = _from_workbook(path, complete if migrate else None)
    elif suffix == ".csv":
        conversations, how = _from_csv(path, complete if migrate else None)
    elif suffix == ".docx":
        conversations, how = _from_docx(path)
    elif suffix == ".pdf":
        conversations, how = _from_pdf(path)
    elif suffix in (".md", ".txt", ".json"):
        conversations, how = _from_text(
            path.read_text(encoding="utf-8", errors="replace"), path.name)
    else:
        raise UnreadableConversations(
            f"{suffix or 'that file type'} is not one this reads. Send the conversations as a "
            f"spreadsheet, a CSV, a Word document, a PDF or plain text.")

    logger.info("Read %d conversation(s) from %s (%s).", len(conversations), path.name, how)
    grouped = sum(1 for c in conversations if c.group)
    if grouped:
        logger.info("%d of them carry the model owner's scenario label, which will be assessed "
                    "rather than taken as given.", grouped)
    return conversations, how
