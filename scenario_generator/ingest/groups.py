"""The kinds of material a model owner submits, and where each lands.

Everything arrives as "documents", but the pieces are not interchangeable. The model documentation
describes the agent; the transcripts of the model owner's testing describe what has already
been exercised; a workflow diagram states the branching that prose leaves implicit. Keeping them
apart means each is read the right way, sent to the stage that needs it, and read in parallel with
the others rather than after them.

Adding a kind means adding one entry here. Nothing else needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

MODEL_DOC = "model_doc"
OWNER_SCENARIOS = "owner_scenarios"
SUPPORTING = "supporting"
DIAGRAMS = "diagrams"


# Written material, in every shape a team sends it. Kept as one tuple because the distinction
# between a group is what the file is *about*, not what format it happens to be in -- a decision
# table is as likely to arrive as a spreadsheet as a Word table, and refusing it for its extension
# is refusing the content.
_DOCUMENTS: Tuple[str, ...] = (".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv", ".md", ".txt")
_IMAGES: Tuple[str, ...] = (".png", ".jpg", ".jpeg")


@dataclass(frozen=True)
class Group:
    """One kind of submitted material, as the person uploading it thinks of it."""

    key: str
    title: str
    blurb: str
    accepts: Tuple[str, ...]
    icon: str = "document"
    multiple: bool = True

    @property
    def accept_attribute(self) -> str:
        """The file picker's filter, so the dialogue offers the right files."""
        return ",".join(self.accepts)


GROUPS: Tuple[Group, ...] = (
    Group(MODEL_DOC, "Model documentation",
          "The document describing the agent: what it does, where it branches, what it must not "
          "do. The main source for everything downstream.",
          _DOCUMENTS, icon="document"),

    Group(OWNER_SCENARIOS, "The model owner's testing",
          "The conversations the model owner has already run, in whatever shape they arrived — "
          "one row per turn, a transcript per row, or a document of exchanges. Any grouping the "
          "model owner applied is read too, and checked rather than taken at face value. Used to "
          "measure how much of the scenario space the model owner's testing already exercises.",
          (".xlsx", ".xlsm", ".csv", ".docx", ".pdf", ".md", ".txt"), icon="checklist"),

    Group(DIAGRAMS, "Workflow diagrams",
          "Screenshots or exports of the agent's flow. Several images of one long flow are read "
          "together as a sequence.",
          _IMAGES, icon="diagram"),

    Group(SUPPORTING, "Supporting material",
          "Vendor documentation, decks, policy extracts, rule tables, anything else that bears on "
          "how the agent behaves.",
          _DOCUMENTS + _IMAGES, icon="folder"),
)

GROUP_BY_KEY: Dict[str, Group] = {group.key: group for group in GROUPS}

# Where a file lands when it arrives without a stated kind -- from the "add a document" form that
# sits on every stage, for the thing that turns up after the pack has been read. Supporting
# material is the right default because it is the group with no assumptions attached: it is read
# as evidence, which is what the person adding a late document wants, and it does not overwrite
# the claim that some particular file is *the* model documentation.
DEFAULT_GROUP = SUPPORTING

# The groups ingestion reads for evidence. The model owner's conversations are not among them:
# they describe the model owner's testing rather than the agent, and reading them as evidence about
# the agent would let the model owner's blind spots into the scenario space through the back door.
EVIDENCE_GROUPS: Tuple[str, ...] = (MODEL_DOC, DIAGRAMS, SUPPORTING)

ALL_EXTENSIONS: Tuple[str, ...] = tuple(sorted(
    {extension for group in GROUPS for extension in group.accepts}))


def folder_for(root: Path, group_key: str) -> Path:
    """Where a group's files live inside a workspace."""
    return Path(root) / "sources" / group_key


def files_in(root: Path, group_key: str) -> List[Path]:
    folder = folder_for(root, group_key)
    return sorted(p for p in folder.glob("*") if p.is_file()) if folder.exists() else []


def evidence_files(root: Path) -> Dict[str, List[Path]]:
    """The submitted material that describes the agent, grouped so it can be read in parallel."""
    return {key: files_in(root, key) for key in EVIDENCE_GROUPS
            if files_in(root, key)}


def owner_scenario_file(root: Path) -> Path:
    """The transcripts of the model owner's testing, if any were submitted."""
    found = files_in(root, OWNER_SCENARIOS)
    return found[0] if found else None


def remove_file(root: Path, group_key: str, name: str) -> bool:
    """Delete one submitted file. Returns whether there was one to delete.

    The name is resolved inside the group's own folder and checked to be there afterwards, so a
    name carrying a path cannot reach anything outside it.
    """
    folder = folder_for(root, group_key).resolve()
    target = (folder / Path(name).name).resolve()
    if folder not in target.parents or not target.is_file():
        return False
    target.unlink()
    return True
