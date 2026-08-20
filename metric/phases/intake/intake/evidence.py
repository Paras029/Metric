"""The evidence record: what the submitted documents were found to say, and where."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

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
    ("owner_testing", "How the model owner tested the agent themselves, and what that "
                      "found."),
    ("terminology", "Domain vocabulary a tester would need in order to write a realistic "
                    "conversation."),
])

# A claim drawn from a diagram cannot be checked against a span of text, so it is recorded as
# unverifiable rather than either trusted or discarded, and always surfaces for confirmation.
KIND_IMAGE = "image"
KIND_HUMAN = "human"

# Which facet is worth reading about first, where several are outstanding. A branch with unnamed
# outcomes cannot be enumerated at all, so nothing on it is ever tested; an undescribed tool costs
# one column. Only an ordering -- what actually blocks the intake is decided structurally from the
# declaration itself, at the intake stage; see :mod:`scenario_generator.core.gaps`.
FACET_ORDER = {"decisions": 0, "states": 1, "capabilities": 2, "use_case": 3,
               "personas": 4, "tools": 5}

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
    """One observation read out of one place in one document."""

    id: str = ""
    facet: str = ""
    statement: str = ""
    quote: str = ""
    source: SourceRef = field(default_factory=lambda: SourceRef(""))
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
class FacetAnswer:
    """What the documents, taken together, establish about one question."""

    facet: str = ""
    answer: str = ""
    points: List[str] = field(default_factory=list)
    unknowns: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    confidence: str = "Low"
    failed: bool = False

    must_ask: List[str] = field(default_factory=list)
    """The subset of ``unknowns`` that stops the intake being filled in.

    Set by the resolution sweep once it has finished putting these back to the documents. What is
    left over at that point divides in two, and the division is the whole reason the list stays
    short: a branch whose outcomes are never named cannot be enumerated and has to be asked about,
    while a threshold nobody wrote down is normal, testable, and not worth anyone's week. Both
    stay under ``unknowns`` so the context document still records everything that was not settled;
    only this list is put in front of a person.
    """


    triaged: bool = False
    """Whether the division above has been made. Where it has not, every unknown is asked."""

    citations: List[dict] = field(default_factory=list)
    """Quotes the reading offered in support, before they are checked.

    Replaced by ``sources`` -- the ids of the claims that survived the check -- once
    verification has run, so what persists is only what was found in the documents.
    """

    @property
    def is_answered(self) -> bool:
        """Whether this question was actually answered."""
        return not self.failed and bool(self.answer.strip() or self.points)


@dataclass
class DocumentRef:
    """A submitted document, recorded so the context file can say what it was built from."""

    name: str
    kind: str
    units: int = 0
    note: str = ""

    is_image: bool = False
    """Whether this was submitted as a picture rather than as text.

    A workflow drawn across five images is one flow, not five documents, and counting it as five
    overstates how much was submitted. Recorded here rather than inferred from ``kind`` because an
    image that could not be read is recorded as unreadable, which loses what it was.
    """

    drawn_on: bool = False
    """Whether any answer actually rested on this document.

    A pack is submitted as a whole and read as a whole, which makes it easy for one long document
    to answer everything and the rest to contribute nothing without anyone noticing. Recording it
    per document turns that into a number a person can see and challenge.
    """


@dataclass
class EvidenceRecord:
    """Everything ingestion established, and from what."""

    documents: List[DocumentRef] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)
    answers: List[FacetAnswer] = field(default_factory=list)

    structure: Dict[str, list] = field(default_factory=dict)
    """The decision graph read out of submitted workflow diagrams, if any were submitted.

    Carried as structure rather than only as prose because a workflow diagram *is* the intake's
    decision and state sheets, and the pass that drafts the intake can confirm and complete a
    graph far more reliably than it can rebuild one from sentences about a graph. The prose
    reading of the same diagrams is in :attr:`claims` alongside it; this is the part that has a
    shape. See :mod:`scenario_generator.ingest.diagram_structure`.
    """

    def answer_for(self, facet: str) -> Optional["FacetAnswer"]:
        return next((a for a in self.answers if a.facet == facet), None)

    def claims_by_id(self) -> Dict[str, Claim]:
        return {c.id: c for c in self.claims if c.id}

    def usable(self) -> List[Claim]:
        return [c for c in self.claims if c.is_usable]

    def by_facet(self, facet: str) -> List[Claim]:
        return [c for c in self.usable() if c.facet == facet]

    def rejected(self) -> List[Claim]:
        return [c for c in self.claims if c.status == REJECTED]

    def needing_confirmation(self) -> List[Claim]:
        return [c for c in self.usable() if c.needs_confirmation]

    def empty_facets(self) -> List[str]:
        """Questions the documents did not answer."""
        if self.answers:
            return [facet for facet in FACETS
                    if not (self.answer_for(facet) and self.answer_for(facet).is_answered)]
        return [facet for facet in FACETS if not self.by_facet(facet)]

    def open_unknowns(self) -> List[Tuple[str, str]]:
        """Every specific thing an answer said it could not settle, as (facet, unknown)."""
        return [(answer.facet, unknown)
                for answer in self.answers for unknown in answer.unknowns]

    def questions_for_people(self) -> List[Tuple[str, str]]:
        """The unknowns worth putting to a person, as (facet, question)."""
        questions = []
        for answer in self.answers:
            questions += [(answer.facet, q)
                          for q in (answer.must_ask if answer.triaged else answer.unknowns)]
        return questions

    def to_dict(self) -> dict:
        return {"documents": [asdict(d) for d in self.documents],
                "claims": [asdict(c) for c in self.claims],
                "answers": [asdict(a) for a in self.answers],
                "structure": dict(self.structure)}

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRecord":
        documents = [DocumentRef(**d) for d in data.get("documents", [])]
        claims = []
        for raw in data.get("claims", []):
            raw = dict(raw)
            claims.append(Claim(source=SourceRef(**raw.pop("source", {})), **raw))
        answers = [FacetAnswer(**a) for a in data.get("answers", [])]
        return cls(documents=documents, claims=claims, answers=answers,
                   structure=data.get("structure") or {})


def summarise(record: EvidenceRecord) -> Dict[str, int]:
    """Counts worth showing a user after ingestion, and worth logging."""
    return {
        "documents": len(record.documents),
        "claims": len(record.claims),
        "usable": len(record.usable()),
        "rejected": len(record.rejected()),
        "needing_confirmation": len(record.needing_confirmation()),
        "empty_facets": len(record.empty_facets()),
        "answered": sum(1 for a in record.answers if a.is_answered),
        "unknowns": len(record.open_unknowns()),
        "to_ask": len(record.questions_for_people()),
        "readable": sum(1 for d in record.documents if d.kind != "unreadable"),
        "drawn_on": sum(1 for d in record.documents if d.drawn_on),
        # Split out because a workflow spread over five images is one flow submitted in five
        # files, and reporting it as five documents read overstates the size of the pack.
        "images": sum(1 for d in record.documents if d.is_image),
        "images_read": sum(1 for d in record.documents if d.is_image and d.kind != "unreadable"),
        "texts": sum(1 for d in record.documents if not d.is_image),
        "texts_read": sum(1 for d in record.documents
                          if not d.is_image and d.kind != "unreadable"),
    }
