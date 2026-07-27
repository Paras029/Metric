"""The kinds of material a model owner submits, and where each lands.

Everything arrives as "documents", but the pieces are not interchangeable. The model
documentation describes the agent; the owner's own scenario library describes their testing of
it; a workflow diagram states the branching that prose leaves implicit. Keeping them apart means
each is read the right way, sent to the stage that needs it, and read in parallel with the
others rather than after them.

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


@dataclass(frozen=True)
class Group:
    """One kind of submitted material, as the person uploading it thinks of it."""

    key: str
    title: str
    blurb: str
    accepts: Tuple[str, ...]
    multiple: bool = True

    @property
    def accept_attribute(self) -> str:
        """The file picker's filter, so the dialogue offers the right files."""
        return ",".join(self.accepts)


GROUPS: Tuple[Group, ...] = (
    Group(MODEL_DOC, "Model documentation",
          "The document describing the agent: what it does, where it branches, what it must not "
          "do. The main source for everything downstream.",
          (".pdf", ".docx", ".md", ".txt")),

    Group(OWNER_SCENARIOS, "Their own test scenarios",
          "Whatever testing the model owner has already done, in whatever shape they sent it. "
          "Used to measure how much of the benchmark they already cover.",
          (".xlsx", ".xlsm", ".csv", ".docx", ".pdf", ".md", ".txt")),

    Group(DIAGRAMS, "Workflow diagrams",
          "Screenshots or exports of the agent's flow. Several images of one long flow are read "
          "together as a sequence.",
          (".png", ".jpg", ".jpeg")),

    Group(SUPPORTING, "Supporting material",
          "Vendor documentation, decks, policy extracts, anything else that bears on how the "
          "agent behaves.",
          (".pdf", ".docx", ".pptx", ".md", ".txt")),
)

GROUP_BY_KEY: Dict[str, Group] = {group.key: group for group in GROUPS}

# The groups ingestion reads for evidence. The owner's scenarios are not among them: they
# describe the owner's testing rather than the agent, and reading them as evidence about the
# agent would let their blind spots into the benchmark through the back door.
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
    """The owner's scenario library, if one was submitted."""
    found = files_in(root, OWNER_SCENARIOS)
    return found[0] if found else None
