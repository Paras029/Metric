"""The evidence record: what the submitted documents were found to say, and where.

This is the machine-readable state produced by document ingestion, and the single input to both
things built from it -- the context document handed to later stages, and the draft intake
workbook. Keeping it as a record of individual claims rather than a block of prose is what makes
the rest of the guarantee possible: every downstream sentence traces to one claim, and every
claim traces to a span of a submitted document.

Nothing here reads a file or calls a model. Parsing lives behind the document reader interface;
extraction is a model call; this module only defines what a claim is and what a set of them
means.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

# What ingestion looks for, and why. The first six mirror the intake's own sheets, so a claim can
# be routed to the cell it informs. The rest exist because scenario generation needs to know what
# the agent is *for* and what the business considers risky, not only how it branches -- the
# review pass reasons about business consequence and cannot do that from structure alone.
FACETS: "OrderedDict[str, str]" = OrderedDict([
    ("use_case", "What the agent is, the business problem it exists to solve, and the intended "
                 "use -- including who it serves and what success looks like."),
    ("personas", "The kinds of user the agent serves, and how their needs or behaviour differ."),
    ("capabilities", "What the agent can do: each distinct function it performs."),
    ("decisions", "Points where the agent branches, and the named outcomes of each branch."),
    ("states", "Positions the interaction can occupy, including where it ends."),
    ("tools", "Systems the agent calls, and whether calling one changes stored state."),
    ("policy_constraints", "Rules the agent must honour: data handling, disclosure, conduct, "
                           "regulatory obligations, mandatory disclaimers."),
    ("scope_boundaries", "What the agent explicitly does not do, and what it must hand to a "
                         "human."),
    ("risk_areas", "Failure modes the business already knows or worries about, including "
                   "anything flagged by a previous review."),
    ("owner_testing", "How the team that built the agent tested it themselves, and what they "
                      "found."),
    ("terminology", "Domain vocabulary a tester would need in order to write a realistic "
                    "conversation."),
])

# A claim drawn from a diagram cannot be checked against a span of text, so it is recorded as
# unverifiable rather than either trusted or discarded, and always surfaces for confirmation.
KIND_IMAGE = "image"
KIND_HUMAN = "human"

VERIFIED = "verified"
UNVERIFIABLE = "unverifiable"
REJECTED = "rejected"


@dataclass(frozen=True)
class SourceRef:
    """Where a claim came from, precisely enough for a reviewer to go and look."""

    document: str
    locator: str = ""
    kind: str = ""

    def __str__(self) -> str:
        return f"{self.document}, {self.locator}" if self.locator else self.document


@dataclass
class Claim:
    """One fact drawn from one place in one document.

    ``statement`` is the fact in the extractor's words; ``quote`` is the span of source text it
    came from. The two are separate so the second can be checked against the document -- a
    statement with no locatable quote is not evidence, whatever it says.
    """

    facet: str
    statement: str
    quote: str
    source: SourceRef
    status: str = VERIFIED
    note: str = ""
    conflicts_with: List[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """Whether this claim may inform anything downstream. Rejected claims never do."""
        return self.status in (VERIFIED, UNVERIFIABLE)

    @property
    def needs_confirmation(self) -> bool:
        """Whether a human must confirm it before it is relied on."""
        return self.status == UNVERIFIABLE or bool(self.conflicts_with)


@dataclass
class DocumentRef:
    """A submitted document, recorded so the context file can say what it was built from."""

    name: str
    kind: str
    units: int = 0
    note: str = ""


@dataclass
class EvidenceRecord:
    """Everything ingestion established, and from what."""

    documents: List[DocumentRef] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)

    def usable(self) -> List[Claim]:
        return [c for c in self.claims if c.is_usable]

    def by_facet(self, facet: str) -> List[Claim]:
        return [c for c in self.usable() if c.facet == facet]

    def rejected(self) -> List[Claim]:
        return [c for c in self.claims if c.status == REJECTED]

    def needing_confirmation(self) -> List[Claim]:
        return [c for c in self.usable() if c.needs_confirmation]

    def empty_facets(self) -> List[str]:
        """Facets no document said anything about.

        This is the gap report's backbone. A facet with no evidence is not an extraction failure
        to be worked around -- it means the submitted pack does not describe something the
        benchmark needs, and the person who submitted it is the one who can fix that.
        """
        return [facet for facet in FACETS if not self.by_facet(facet)]

    def to_dict(self) -> dict:
        return {"documents": [asdict(d) for d in self.documents],
                "claims": [asdict(c) for c in self.claims]}

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRecord":
        documents = [DocumentRef(**d) for d in data.get("documents", [])]
        claims = []
        for raw in data.get("claims", []):
            raw = dict(raw)
            claims.append(Claim(source=SourceRef(**raw.pop("source", {})), **raw))
        return cls(documents=documents, claims=claims)


def group_by_facet(claims: Iterable[Claim]) -> "OrderedDict[str, List[Claim]]":
    """Claims arranged in the order the facets are declared, for stable rendering."""
    grouped: "OrderedDict[str, List[Claim]]" = OrderedDict((facet, []) for facet in FACETS)
    for claim in claims:
        grouped.setdefault(claim.facet, []).append(claim)
    return grouped


def summarise(record: EvidenceRecord) -> Dict[str, int]:
    """Counts worth showing a user after ingestion, and worth logging."""
    return {
        "documents": len(record.documents),
        "claims": len(record.claims),
        "usable": len(record.usable()),
        "rejected": len(record.rejected()),
        "needing_confirmation": len(record.needing_confirmation()),
        "empty_facets": len(record.empty_facets()),
    }


def find_conflicts(claims: List[Claim]) -> None:
    """Placeholder for cross-document conflict detection, recorded on the claims themselves.

    Conflicts are surfaced rather than resolved: where a vendor document and a model document
    disagree, both claims survive and a human decides. Implemented once extraction is in place,
    since what counts as a conflict depends on how statements are phrased.
    """
    raise NotImplementedError("conflict detection lands with the extraction pass")


def optional(value: Optional[str]) -> str:
    """Normalise an absent string field to the empty string, for consistent record shape."""
    return (value or "").strip()
