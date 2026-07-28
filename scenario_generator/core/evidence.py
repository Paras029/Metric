"""The evidence record: what the submitted documents were found to say, and where.

Two layers, and the distinction between them is the point.

A **claim** is one observation read out of one passage: a statement, the excerpt supporting it,
and the document and locator it came from. Claims are checked against their source, so a claim is
something the documentation demonstrably says.

A **facet answer** is the synthesis of every claim bearing on one of the questions this pipeline
needs answered. Real documentation does not answer those questions in one place -- what the agent
decides, and where it branches, is spread over pages that each describe a fragment. An answer is
therefore allowed to collect and restructure, which a claim is not, and it records which claims it
rests on so the restructuring can be traced back.

Keeping the two apart is what lets the context document say more than any single sentence of the
source while still being auditable to the page.

Nothing here reads a file or calls a model. Parsing lives behind the document reader interface;
extraction and synthesis are model calls; this module only defines what the results are.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

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

# The parts of the intake a question can block. A question that cannot name one of these is not
# asked: the intake can be filled without it, and that is the only test that matters once it is
# accepted that documentation is never complete.
INTAKE_PARTS = ("use_case", "personas", "capabilities", "decisions", "states", "tools")

# Which blocked part is worth asking about first. A branch with unnamed outcomes cannot be
# enumerated at all, so nothing on it is ever tested; an undescribed tool costs one column.
BLOCKING_ORDER = {"decisions": 0, "states": 1, "capabilities": 2, "use_case": 3,
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
    """One observation read out of one place in one document.

    ``statement`` is the fact in the extractor's words; ``quote`` is the span of source text it
    came from. The two are separate so the second can be checked against the document -- a
    statement with no locatable quote is not evidence, whatever it says.
    """

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
    """What the documents, taken together, establish about one question.

    ``answer`` and ``points`` may restructure and combine what several claims say -- that is the
    reason this layer exists. ``sources`` names the claims it was built from, and ``unknowns``
    records what the documents did not settle, stated openly rather than left as an absence
    nobody notices.
    """

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

    blocks: Dict[str, str] = field(default_factory=dict)
    """Which part of the intake each asked question blocks, keyed by the question.

    A question earns its place by naming what it stops -- one of the intake's own six parts. This
    is both the justification shown to the reader and the filter: a question that cannot name what
    it blocks is not asked, because documentation is always incomplete and "this would be good to
    know" is not a reason to spend a modelling team's fortnight.
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
        """Whether this question was actually answered.

        A synthesis that failed keeps the raw observations under ``points`` so the material is not
        lost, but it is not an answer and must not be counted as one. Reporting a failed run as
        complete is worse than the failure: the failure is visible and fixable, and the false
        success is neither.
        """
        return not self.failed and bool(self.answer.strip() or self.points)


@dataclass
class DocumentRef:
    """A submitted document, recorded so the context file can say what it was built from."""

    name: str
    kind: str
    units: int = 0
    note: str = ""

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
        """Questions the documents did not answer.

        This is the gap report's backbone. A question with no answer is not an extraction failure
        to be worked around -- it means the submitted pack does not describe something the
        benchmark needs, and the person who submitted it is the one who can fix that.

        Judged on the synthesised answer where synthesis has run, because scattered observations
        that were never assembled into an answer are not, in any useful sense, an answer.
        """
        if self.answers:
            return [facet for facet in FACETS
                    if not (self.answer_for(facet) and self.answer_for(facet).is_answered)]
        return [facet for facet in FACETS if not self.by_facet(facet)]

    def open_unknowns(self) -> List[Tuple[str, str]]:
        """Every specific thing an answer said it could not settle, as (facet, unknown)."""
        return [(answer.facet, unknown)
                for answer in self.answers for unknown in answer.unknowns]

    def questions_for_people(self) -> List[Tuple[str, str]]:
        """The unknowns worth putting to a person, as (facet, question).

        Where an answer has been triaged this is the subset that blocks the intake; where it has
        not -- an older record, or a run whose resolution sweep did not complete -- it is
        everything, because the alternative is quietly dropping questions nobody has judged.
        """
        questions = []
        for answer in self.answers:
            questions += [(answer.facet, q)
                          for q in (answer.must_ask if answer.triaged else answer.unknowns)]
        return questions

    def blocked_part(self, question: str) -> str:
        """Which part of the intake this question stops being filled in, if it was recorded."""
        for answer in self.answers:
            found = answer.blocks.get(question)
            if found:
                return found
        return ""

    def set_aside(self) -> int:
        """How many unknowns triage judged not worth asking. Reported rather than hidden."""
        return sum(len(a.unknowns) - len(a.must_ask) for a in self.answers if a.triaged)

    def to_dict(self) -> dict:
        return {"documents": [asdict(d) for d in self.documents],
                "claims": [asdict(c) for c in self.claims],
                "answers": [asdict(a) for a in self.answers]}

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRecord":
        documents = [DocumentRef(**d) for d in data.get("documents", [])]
        claims = []
        for raw in data.get("claims", []):
            raw = dict(raw)
            claims.append(Claim(source=SourceRef(**raw.pop("source", {})), **raw))
        answers = [FacetAnswer(**a) for a in data.get("answers", [])]
        return cls(documents=documents, claims=claims, answers=answers)


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
        "answered": sum(1 for a in record.answers if a.is_answered),
        "unknowns": len(record.open_unknowns()),
        "to_ask": len(record.questions_for_people()),
        "set_aside": record.set_aside(),
        "readable": sum(1 for d in record.documents if d.kind != "unreadable"),
        "drawn_on": sum(1 for d in record.documents if d.drawn_on),
    }
