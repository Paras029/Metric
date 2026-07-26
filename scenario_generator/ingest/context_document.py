"""Building the context document and the open questions from a verified evidence record.

The context document is **assembled, not written**. Every line comes from a claim that survived
the grounding check, carries the document and page it came from, and appears in an order fixed by
the facet list. No model is involved at this point, which is what lets the file be handed to
later stages as fact rather than as another opinion to weigh.

That is a deliberate trade. Assembled prose reads as a cited briefing rather than a narrative.
Its primary reader is another model, and every line being traceable is worth more here than the
text flowing well. A rewritten version would be a model paraphrasing verified claims, which
reintroduces exactly the misphrasing this design exists to prevent.

The open questions are the same record read from the other side: what the documents did not say.
"""
from __future__ import annotations

from typing import List

from ..core.evidence import FACETS, Claim, EvidenceRecord, group_by_facet

FACET_HEADINGS = {
    "use_case": "What the agent is for",
    "personas": "Who it serves",
    "capabilities": "What it can do",
    "decisions": "Where it branches",
    "states": "Where an interaction can be",
    "tools": "Systems it calls",
    "policy_constraints": "Rules it must honour",
    "scope_boundaries": "What it does not do",
    "risk_areas": "Known risk areas",
    "owner_testing": "How its own team tested it",
    "terminology": "Domain vocabulary",
}

# What to ask when a facet came back empty. Phrased as a request to a person, because that is
# what it is -- these go to whoever submitted the pack.
FACET_QUESTIONS = {
    "use_case": "What is this agent for, and what does success look like for the business?",
    "personas": "Which kinds of user does the agent serve, and how do their needs differ?",
    "capabilities": "What distinct things can the agent do?",
    "decisions": "Where does the agent branch, and what are the named outcomes of each branch?",
    "states": "What positions can an interaction be in, and which of them end it?",
    "tools": "Which systems does the agent call, and which of those change stored data?",
    "policy_constraints": "What rules must the agent honour on data, disclosure and conduct?",
    "scope_boundaries": "What is the agent explicitly not allowed to do, and when must it hand "
                        "over to a person?",
    "risk_areas": "What failure modes are already known or suspected for this agent?",
    "owner_testing": "How has the team that built the agent tested it so far, and what did they "
                     "find?",
    "terminology": "What domain terms would a tester need in order to write a realistic "
                   "conversation?",
}


def _citation(claim: Claim) -> str:
    return f"[{claim.source}]" if str(claim.source) else ""


def build_context_document(record: EvidenceRecord, use_case_name: str = "") -> str:
    """Render the evidence record as a cited briefing, in a fixed order."""
    lines: List[str] = []
    title = use_case_name.strip() or "the agent under validation"
    lines.append(f"# Context for {title}")
    lines.append("")
    lines.append("Assembled from the documents listed below. Every statement is quoted or "
                 "paraphrased from a passage that was checked against its source; anything that "
                 "could not be traced was discarded rather than included.")
    lines.append("")

    lines.append("## Documents this was built from")
    lines.append("")
    if record.documents:
        for document in record.documents:
            detail = f"{document.kind}, {document.units} sections" if document.units else document.kind
            note = f" — {document.note}" if document.note else ""
            lines.append(f"- **{document.name}** ({detail}){note}")
    else:
        lines.append("- none")
    lines.append("")

    grouped = group_by_facet(record.usable())
    for facet in FACETS:
        claims = grouped.get(facet) or []
        if not claims:
            continue
        lines.append(f"## {FACET_HEADINGS.get(facet, facet)}")
        lines.append("")
        for claim in claims:
            marker = " *(unconfirmed)*" if claim.needs_confirmation else ""
            lines.append(f"- {claim.statement}{marker} {_citation(claim)}".rstrip())
        lines.append("")

    missing = record.empty_facets()
    if missing:
        lines.append("## Not covered by the submitted documents")
        lines.append("")
        lines.append("Nothing in the pack addressed the following. Treat these as unknown rather "
                     "than as absent from the agent.")
        lines.append("")
        for facet in missing:
            lines.append(f"- {FACET_HEADINGS.get(facet, facet)}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def open_questions(record: EvidenceRecord) -> List[dict]:
    """What still needs answering, as a list a person can work through.

    Two kinds, deliberately kept in one list. A facet nothing addressed is a question about the
    submitted pack. A claim that could not be checked -- read off a diagram, or supplied by
    someone earlier -- is a question about a specific statement. Both block the same thing: an
    intake that can be trusted.
    """
    questions: List[dict] = []

    for facet in record.empty_facets():
        questions.append({
            "kind": "gap",
            "facet": facet,
            "heading": FACET_HEADINGS.get(facet, facet),
            "question": FACET_QUESTIONS.get(facet, f"What does the agent do about {facet}?"),
            "detail": "No submitted document addressed this.",
        })

    for claim in record.needing_confirmation():
        questions.append({
            "kind": "confirm",
            "facet": claim.facet,
            "heading": FACET_HEADINGS.get(claim.facet, claim.facet),
            "question": f"Is this correct? {claim.statement}",
            "detail": claim.note or f"Taken from {claim.source}, and not checkable against text.",
        })

    return questions


def rejection_summary(record: EvidenceRecord) -> str:
    """One line on what was thrown away, for the run log."""
    rejected = record.rejected()
    if not rejected:
        return "Every extracted claim was supported by its source."
    return (f"{len(rejected)} claim(s) cited text that could not be found in the document and "
            f"were discarded.")
