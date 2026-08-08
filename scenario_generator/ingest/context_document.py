"""Building the context document and the open questions from a synthesised evidence record.

The context document is what every later stage reads to understand the agent, and what the intake
is drafted from. It is organised as answers to the questions the benchmark depends on, because
that is the form the work downstream actually needs — not a list of the sentences that happened
to appear in the source.

Assembly here is deterministic. The synthesis pass did the combining and restructuring, under
instruction to rest only on verified observations and to cite them; this module lays that out and
attaches the citations. No model call happens at this point, so the file can be handed on as a
record of what was established rather than as another opinion to weigh.

The supporting observations are printed beneath each answer with their document and page. That is
what makes the restructuring auditable: a reader who doubts a sentence can follow it to the pages
it was built from.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import List

from ..core.evidence import FACET_ORDER, FACETS, EvidenceRecord
from . import diagram_structure

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
    "owner_testing": "How the model owner tested it",
    "terminology": "Domain vocabulary",
}

# What to ask when a question came back unanswered. Phrased as a request to a person, because
# that is what it is -- these go to whoever submitted the pack.
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
    "owner_testing": "How has the model owner tested the agent so far, and what did that "
                     "testing find?",
    "terminology": "What domain terms would a tester need in order to write a realistic "
                   "conversation?",
}


def build_context_document(record: EvidenceRecord, use_case_name: str = "") -> str:
    """Render the evidence record as an organised, cited briefing."""
    lines: List[str] = []
    title = use_case_name.strip() or "the agent under validation"

    lines += [f"# Context for {title}", "",
              "Assembled from the documents listed below by reading each of them for what it "
              "establishes about the agent, then answering each question from everything found "
              "across all of them. Supporting observations are listed under each answer with the "
              "page they came from; every one was checked against its source before use.", ""]

    lines += ["## Documents this was built from", ""]
    if record.documents:
        for document in record.documents:
            detail = (f"{document.kind}, {document.units} sections" if document.units
                      else document.kind)
            note = f" — {document.note}" if document.note else ""
            # Said per document rather than only in aggregate: a submitted file that informed
            # nothing is either irrelevant or overlooked, and which of the two it is matters.
            if document.kind != "unreadable" and not document.drawn_on:
                note += " — *nothing below rests on this document*"
            lines.append(f"- **{document.name}** ({detail}){note}")
    else:
        lines.append("- none")
    lines.append("")

    if not diagram_structure.is_empty(record.structure or {}):
        lines += ["## The workflow, as read from the submitted diagrams", "",
                  "Read off the images box by box, then joined across them. Nothing here was "
                  "checked against text -- a diagram cannot be quoted -- so treat it as a first "
                  "draft of the structure to confirm rather than as established fact.", "",
                  "```", diagram_structure.render(record.structure), "```", ""]

    claims = record.claims_by_id()
    answered = [f for f in FACETS
                if record.answer_for(f) and record.answer_for(f).is_answered]

    for facet in answered:
        answer = record.answer_for(facet)
        lines += [f"## {FACET_HEADINGS.get(facet, facet)}", ""]
        if answer.confidence and answer.confidence != "High":
            lines += [f"*Confidence: {answer.confidence.lower()} — the documents cover this "
                      f"only partly.*", ""]
        if answer.answer:
            lines += [answer.answer, ""]

        if answer.points:
            lines += ["**Specifics**", ""]
            lines += [f"- {point}" for point in answer.points]
            lines.append("")

        if answer.unknowns:
            lines += ["**Not settled by the documents**", ""]
            lines += [f"- {unknown}" for unknown in answer.unknowns]
            lines.append("")

        supporting = [claims[cid] for cid in answer.sources if cid in claims]
        if supporting:
            lines += ["<details><summary>Supporting observations</summary>", ""]
            for claim in supporting:
                marker = " *(unconfirmed)*" if claim.needs_confirmation else ""
                lines.append(f"- `{claim.id}` {claim.statement}{marker} — {claim.source}")
            lines += ["", "</details>", ""]

    missing = record.empty_facets()
    if missing:
        lines += ["## Not covered by the submitted documents", "",
                  "Nothing in the pack settled the following. Treat these as unknown rather than "
                  "as absent from the agent — a gap in the documentation and a deliberate "
                  "exclusion have very different consequences for a benchmark.", ""]
        lines += [f"- **{FACET_HEADINGS.get(facet, facet)}** — "
                  f"{FACET_QUESTIONS.get(facet, '')}" for facet in missing]
        lines.append("")

    return "\n".join(lines).strip() + "\n"


# Why an unanswered facet matters, in terms of what the intake cannot say without it.
FACET_STAKES = {
    "use_case": "Without this the intake cannot say what the agent is for.",
    "personas": "Without this the intake cannot say who the agent serves.",
    "capabilities": "Without this a capability cannot be listed in the intake.",
    "decisions": "Without this a branch cannot be enumerated, so nothing on it gets tested.",
    "states": "Without this an outcome leads somewhere the intake cannot describe.",
    "tools": "Without this the intake cannot say what calling the system does.",
}


def _confirmation_groups(record: EvidenceRecord) -> List[dict]:
    """Claims needing confirmation, one entry per facet rather than one per claim.

    A single workflow diagram routinely reads out several dozen individual observations, and none
    of them changes what the intake needs to declare -- each one needs the same thing, a person's
    glance to say it was read right. Listing every one as its own question turns one diagram into
    a wall of near-identical rows; grouping them by the part of the intake they inform is the same
    review; with a fraction of the scrolling.
    """
    grouped: "OrderedDict[str, list]" = OrderedDict()
    for claim in record.needing_confirmation():
        grouped.setdefault(claim.facet, []).append(claim)

    groups = []
    for facet, claims in grouped.items():
        plural = "" if len(claims) == 1 else "s"
        groups.append({
            "kind": "confirm",
            "facet": facet,
            "blocks": "",
            "heading": FACET_HEADINGS.get(facet, facet),
            "question": f"Confirm {len(claims)} statement{plural} for "
                        f"{FACET_HEADINGS.get(facet, facet).lower()}, read from a source that "
                        f"cannot be checked against text (a diagram, usually)",
            "detail": "\n".join(f"• {claim.statement}" for claim in claims),
        })
    return groups


def open_questions(record: EvidenceRecord) -> List[dict]:
    """What still needs answering, as a list a person can work through, most blocking first.

    Three kinds, deliberately in one list because they block the same thing — an intake that can
    be trusted. A question nothing addressed is a gap in the submitted pack. A specific point an
    answer could not settle is a gap inside an otherwise good answer, and is usually the more
    useful of the two, since it is precise enough to be answered in a sentence. A statement that
    could not be checked against text needs confirming before anything rests on it -- these are
    grouped one entry per facet rather than one per statement, since a diagram alone can produce
    dozens and every one of them asks the same thing of the reader.

    Only the unknowns the resolution sweep judged to stop the intake being filled in appear here.
    Everything else stays in the evidence record and in the context document, where it is a note
    on how complete the documentation is rather than a task for anybody. Documentation is always
    incomplete; the questions worth a model owner's time are the ones without which a part of
    the intake cannot be written at all.
    """
    questions: List[dict] = []
    unanswered = set(record.empty_facets())

    for facet in record.empty_facets():
        questions.append({
            "kind": "gap",
            "facet": facet,
            "blocks": facet if facet in FACET_STAKES else "",
            "heading": FACET_HEADINGS.get(facet, facet),
            "question": FACET_QUESTIONS.get(facet, f"What does the agent do about {facet}?"),
            "detail": "No submitted document settled this.",
        })

    # An unanswered question already appears above as a gap, and the unknown it carries is the
    # same question worded the same way -- listing both doubles the length of the list without
    # adding anything to answer. Only a facet that *was* answered has unknowns worth raising
    # separately, because those are the specific points a good answer could not settle.
    seen = {q["question"].strip().lower() for q in questions}
    for facet, unknown in record.questions_for_people():
        if facet in unanswered or unknown.strip().lower() in seen:
            continue
        seen.add(unknown.strip().lower())
        questions.append({
            "kind": "unknown",
            "facet": facet,
            "blocks": "",
            "heading": FACET_HEADINGS.get(facet, facet),
            "question": unknown,
            "detail": "The documents answered this question in part, but not this.",
        })

    questions += _confirmation_groups(record)

    kinds = {"gap": 0, "unknown": 1, "confirm": 2}
    questions.sort(key=lambda q: (kinds.get(q["kind"], 3),
                                  FACET_ORDER.get(q["facet"], len(FACET_ORDER))))
    return questions


def rejection_summary(record: EvidenceRecord) -> str:
    """One line on what was thrown away, for the run log."""
    rejected = record.rejected()
    if not rejected:
        return "Every observation was supported by its source."
    return (f"{len(rejected)} observation(s) cited text that could not be found in the document "
            f"and were discarded.")
