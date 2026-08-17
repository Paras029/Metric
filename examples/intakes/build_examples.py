"""Four intake workbooks, built to exercise capability spans from different angles.

Each one is a real-shaped agent rather than a minimal graph, because the thing under test is what
happens at depth: a shallow graph hides both the explosion these spans exist to stop and the ways
a span can be drawn wrongly.

    python examples/intakes/build_examples.py [output-dir]

Every file it writes is a valid intake workbook. Upload one at the intake stage, run Workflow, and
compare what comes out against the notes in CHECKS.md beside them.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openpyxl import load_workbook

from scenario_generator.core.intake import write_template


def build(path: Path, use_case, personas, capabilities, decisions, states, tools) -> Path:
    write_template(str(path))
    book = load_workbook(path)
    sheet = book["L1 Use Case"]
    at = {str(sheet.cell(row=r, column=1).value or "").strip(): r
          for r in range(2, sheet.max_row + 1)}
    for label, value in use_case.items():
        if label in at:
            sheet.cell(row=at[label], column=2, value=value)
    for row in personas:
        book["Personas"].append(row)
    for row in capabilities:
        book["L2 Capabilities"].append(row)
    for row in decisions:
        book["L3 Decisions"].append(row)
    for row in states:
        book["L4 States"].append(row)
    for row in tools:
        book["Tools"].append(row)
    book.save(path)
    return path


# --------------------------------------------------------------------------- 1. the canonical one
def disputes(directory: Path) -> Path:
    """Three capabilities in sequence, the second entered two ways.

    The shape the whole change was designed around: identification hands on at two different
    positions, so verification is tested from each, and charge handling is tested once from the
    one position that reaches it.
    """
    return build(
        directory / "1_disputes_three_blocks.xlsx",
        {"Use case name": "Cardmember disputes assistant",
         "Business objective": "Resolve disputed card charges without a person",
         "Agent type": "Conversational assistant", "Channel / modality": "Mobile app chat",
         "Human handoff triggers": "Identity cannot be established; cardmember asks for a person",
         "Safety requirements": "Never disclose full card numbers; never reverse a charge without "
                                "verification",
         "Success criteria": "A dispute is filed or refused with a stated reason"},
        [["P1", "Cardmember", "Wants a charge investigated", "Y"],
         ["P-ADV", "Adversarial user", "Trying to move money off another person's account", ""]],
        [["CAP-01", "Identification", "Gating", "S-00", "S-02, S-03, S-04"],
         ["CAP-02", "Verification", "Gating", "S-02, S-03", "S-07, S-08"],
         ["CAP-03", "Charge handling", "Transactional", "S-07", "S-11, S-12, S-13"]],
        [["DEC-01", "Read the opening request", "CAP-01", "opening message",
          "Dispute / Other intent", "User", 1, "the request names a charge"],
         ["DEC-02", "Identify the cardmember", "CAP-01", "card last four, date of birth",
          "By card details / By one-time code / Cannot identify", "User", 3,
          "details match the account on file"],
         ["DEC-03", "Verify recent activity", "CAP-02", "recent transaction recall",
          "Verified / Failed", "User", 2, "cardmember recalls a recent charge"],
         ["DEC-04", "Check the account standing", "CAP-02", "account flags",
          "Clear / Restricted", "System-Context", 1, "no fraud hold on the account"],
         ["DEC-05", "Check the dispute window", "CAP-03", "charge date",
          "Within window / Outside window", "System-Context", 1, "charge is under 60 days old"],
         ["DEC-06", "Classify the dispute", "CAP-03", "reason given",
          "Unauthorised / Service issue / Duplicate", "User", 1, ""]],
        [["S-00", "Start", "The chat opens with an unidentified cardmember", "DEC-01", "No", ""],
         ["S-01", "DEC-01=Dispute", "A dispute has been asked for", "DEC-02", "No", ""],
         ["S-09", "DEC-01=Other intent", "Not a dispute; handed to general servicing", "",
          "Yes", "Fallback"],
         ["S-02", "DEC-02=By card details", "Identified from card details", "DEC-03", "No", ""],
         ["S-03", "DEC-02=By one-time code", "Identified by one-time code", "DEC-03", "No", ""],
         ["S-04", "DEC-02=Cannot identify", "Could not be identified; locked out", "",
          "Yes", "Termination"],
         ["S-05", "DEC-03=Verified", "Recent activity confirmed", "DEC-04", "No", ""],
         ["S-08", "DEC-03=Failed", "Could not confirm activity; handed to an agent", "",
          "Yes", "Escalation"],
         ["S-07", "DEC-04=Clear", "Verified cardmember on a clear account", "DEC-05", "No", ""],
         ["S-10", "DEC-04=Restricted", "Account restricted; handed to fraud", "",
          "Yes", "Escalation"],
         ["S-06", "DEC-05=Within window", "Charge is disputable", "DEC-06", "No", ""],
         ["S-13", "DEC-05=Outside window", "Refused: outside the dispute window", "",
          "Yes", "Fallback"],
         ["S-11", "DEC-06=Unauthorised", "Filed as an unauthorised charge", "", "Yes",
          "Happy path"],
         ["S-12", "DEC-06=Service issue", "Filed as a service dispute", "", "Yes", "Happy path"],
         ["S-14", "DEC-06=Duplicate", "Filed as a duplicate charge", "", "Yes", "Happy path"]],
        [["Identity service", "CAP-01", "No"],
         ["Transaction history", "CAP-02", "No"],
         ["Dispute filing system", "CAP-03", "Yes"]])


# --------------------------------------------------------------------------- 2. no spans at all
def travel(directory: Path) -> Path:
    """The same depth, with no spans drawn. The fallback path, and the before picture.

    Run this one first: it is what the tool did before capabilities had spans, and the scenario
    count it produces is the number the change is meant to bring down.
    """
    return build(
        directory / "2_travel_no_spans.xlsx",
        {"Use case name": "Travel booking assistant",
         "Business objective": "Change and cancel existing bookings without a person",
         "Agent type": "Conversational assistant", "Channel / modality": "Web chat",
         "Human handoff triggers": "Fare rules cannot be applied automatically",
         "Safety requirements": "Never cancel a booking without explicit confirmation",
         "Success criteria": "A booking is changed, cancelled, or refused with a reason"},
        [["P1", "Traveller", "Wants a booking changed", "Y"],
         ["P-ADV", "Adversarial user", "Trying to change somebody else's booking", ""]],
        # Declared, deliberately unbounded -- the tool should walk the whole graph.
        [["CAP-01", "Booking lookup", "Lookup", "", ""],
         ["CAP-02", "Fare rules", "Gating", "", ""],
         ["CAP-03", "Rebooking", "Transactional", "", ""]],
        [["DEC-01", "Find the booking", "CAP-01", "reference or name",
          "Found / Not found", "User", 2, "reference matches a live booking"],
         ["DEC-02", "Confirm the traveller", "CAP-01", "name and departure date",
          "Confirmed / Mismatch", "User", 2, ""],
         ["DEC-03", "Check the fare rules", "CAP-02", "fare class",
          "Changeable / Fee applies / Not changeable", "System-Context", 1, ""],
         ["DEC-04", "Check availability", "CAP-03", "requested date",
          "Available / Full", "Tool", 1, ""],
         ["DEC-05", "Confirm the change", "CAP-03", "explicit confirmation",
          "Confirmed / Declined", "User", 1, "traveller says yes to the fee and the new time"]],
        [["S-00", "Start", "The chat opens", "DEC-01", "No", ""],
         ["S-01", "DEC-01=Found", "Booking located", "DEC-02", "No", ""],
         ["S-02", "DEC-01=Not found", "No booking found; nothing to change", "", "Yes",
          "Termination"],
         ["S-03", "DEC-02=Confirmed", "Traveller confirmed as the booking holder", "DEC-03",
          "No", ""],
         ["S-04", "DEC-02=Mismatch", "Details do not match; refused", "", "Yes", "Termination"],
         ["S-05", "DEC-03=Changeable", "Change permitted at no cost", "DEC-04", "No", ""],
         ["S-06", "DEC-03=Fee applies", "Change permitted with a fee", "DEC-04", "No", ""],
         ["S-07", "DEC-03=Not changeable", "Fare cannot be changed; handed to an agent", "",
          "Yes", "Escalation"],
         ["S-08", "DEC-04=Available", "Requested flight has seats", "DEC-05", "No", ""],
         ["S-09", "DEC-04=Full", "Requested flight is full", "", "Yes", "Fallback"],
         ["S-10", "DEC-05=Confirmed", "Booking changed", "", "Yes", "Happy path"],
         ["S-11", "DEC-05=Declined", "Traveller declined; booking left as it was", "", "Yes",
          "Fallback"]],
        [["Booking system", "CAP-01", "No"],
         ["Fare engine", "CAP-02", "No"],
         ["Rebooking service", "CAP-03", "Yes"]])


# --------------------------------------------------------------------------- 3. deep, with retries
def onboarding(directory: Path) -> Path:
    """Four capabilities, retries inside two of them, and a loop back to an earlier block.

    Built to make the difference obvious: walked whole this runs into the hundreds, walked block
    by block it stays small. It also carries the two cases most likely to be got wrong -- a
    decision behind a retry limit, and a route that goes backwards.
    """
    return build(
        directory / "3_onboarding_deep_with_retries.xlsx",
        {"Use case name": "Account onboarding assistant",
         "Business objective": "Take a new applicant from first contact to an opened account",
         "Agent type": "Conversational assistant", "Channel / modality": "Web chat",
         "Human handoff triggers": "Documents fail twice; applicant is flagged by screening",
         "Safety requirements": "Never store document images in the transcript",
         "Success criteria": "An account is opened, deferred, or refused with a reason"},
        [["P1", "Applicant", "Wants an account opened", "Y"],
         ["P-ADV", "Adversarial user", "Applying under another person's identity", ""]],
        [["CAP-01", "Eligibility", "Gating", "S-00", "S-02, S-03"],
         ["CAP-02", "Document capture", "PII-handling", "S-02", "S-06, S-07"],
         ["CAP-03", "Screening", "Gating", "S-06", "S-10, S-11, S-12"],
         ["CAP-04", "Account opening", "Transactional", "S-10", "S-15, S-16"]],
        [["DEC-01", "Check the applicant's country", "CAP-01", "stated country",
          "Supported / Not supported", "User", 1, ""],
         ["DEC-02", "Check the applicant's age", "CAP-01", "date of birth",
          "Eligible / Under age", "User", 2, "applicant is 18 or over"],
         ["DEC-03", "Capture the identity document", "CAP-02", "document upload",
          "Readable / Unreadable", "Document", 3, "document scan passes quality checks"],
         ["DEC-04", "Match the document to the applicant", "CAP-02", "name and date of birth",
          "Matches / Mismatch", "System-Context", 2, ""],
         ["DEC-05", "Run sanctions screening", "CAP-03", "name and country",
          "Clear / Possible match", "Tool", 1, ""],
         ["DEC-06", "Assess the risk score", "CAP-03", "screening output",
          "Low / Medium / High", "System-Context", 1, ""],
         ["DEC-07", "Choose the product", "CAP-04", "stated need",
          "Everyday / Savings", "User", 1, ""],
         ["DEC-08", "Confirm the terms", "CAP-04", "explicit acceptance",
          "Accepted / Declined", "User", 2, "applicant accepts the account terms"]],
        [["S-00", "Start", "An applicant opens the chat", "DEC-01", "No", ""],
         ["S-01", "DEC-01=Supported", "Applicant is in a supported country", "DEC-02", "No", ""],
         ["S-03", "DEC-01=Not supported", "Refused: country not served", "", "Yes",
          "Termination"],
         ["S-02", "DEC-02=Eligible", "Applicant is eligible to apply", "DEC-03", "No", ""],
         ["S-04", "DEC-02=Under age", "Refused: under age", "", "Yes", "Termination"],
         ["S-05", "DEC-03=Readable", "A readable document has been captured", "DEC-04", "No", ""],
         ["S-07", "DEC-03=Unreadable", "Document could not be read; handed to an agent", "",
          "Yes", "Escalation"],
         ["S-06", "DEC-04=Matches", "Document matches the applicant", "DEC-05", "No", ""],
         # Goes backwards: a mismatch sends the applicant back to capture a document again.
         ["S-08", "DEC-04=Mismatch", "Document does not match; capture it again", "DEC-03",
          "No", ""],
         ["S-09", "DEC-05=Clear", "Screening returned nothing", "DEC-06", "No", ""],
         ["S-12", "DEC-05=Possible match", "Possible sanctions match; handed to compliance", "",
          "Yes", "Escalation"],
         ["S-10", "DEC-06=Low", "Low risk; may proceed", "DEC-07", "No", ""],
         ["S-13", "DEC-06=Medium", "Medium risk; deferred for manual review", "", "Yes",
          "Fallback"],
         ["S-11", "DEC-06=High", "High risk; refused", "", "Yes", "Termination"],
         ["S-14", "DEC-07=Everyday", "Everyday account chosen", "DEC-08", "No", ""],
         ["S-17", "DEC-07=Savings", "Savings account chosen", "DEC-08", "No", ""],
         ["S-15", "DEC-08=Accepted", "Account opened", "", "Yes", "Happy path"],
         ["S-16", "DEC-08=Declined", "Terms declined; application abandoned", "", "Yes",
          "Fallback"]],
        [["Country rules service", "CAP-01", "No"],
         ["Document reader", "CAP-02", "No"],
         ["Sanctions screening", "CAP-03", "No"],
         ["Core banking", "CAP-04", "Yes"]])


# --------------------------------------------------------------------------- 4. drawn wrongly
def broken(directory: Path) -> Path:
    """Spans drawn wrongly, four different ways, on an otherwise sound graph.

    Every one of these is a mistake somebody will make on their first real use case, and the point
    of the file is that none of them should fail silently:

    * CAP-01 names an exit state that does not exist (S-99).
    * CAP-02 has an entry but no exit.
    * CAP-03's entry is not reachable from any other block's exit, so nothing hands on to it.
    * CAP-04 overlaps CAP-03 -- both claim S-05 -- which is legal but doubles that block.
    """
    return build(
        directory / "4_spans_drawn_wrongly.xlsx",
        {"Use case name": "Billing enquiry assistant",
         "Business objective": "Answer billing questions and raise adjustments",
         "Agent type": "Conversational assistant", "Channel / modality": "Web chat",
         "Human handoff triggers": "Adjustment above the automatic limit",
         "Safety requirements": "Never state an account balance before identification",
         "Success criteria": "The question is answered or an adjustment is raised"},
        [["P1", "Account holder", "Wants a bill explained", "Y"],
         ["P-ADV", "Adversarial user", "Fishing for another person's billing detail", ""]],
        [["CAP-01", "Identification", "Gating", "S-00", "S-01, S-99"],
         ["CAP-02", "Bill retrieval", "Lookup", "S-01", ""],
         ["CAP-03", "Explanation", "Advisory", "S-04", "S-06, S-07"],
         ["CAP-04", "Adjustment", "Transactional", "S-04", "S-06, S-08"]],
        [["DEC-01", "Identify the account holder", "CAP-01", "account number, postcode",
          "Identified / Cannot identify", "User", 2, ""],
         ["DEC-02", "Retrieve the bill", "CAP-02", "billing period",
          "Found / Not issued yet", "Tool", 1, ""],
         ["DEC-03", "Classify the question", "CAP-03", "stated question",
          "Explain a charge / Request an adjustment", "User", 1, ""],
         ["DEC-04", "Check the adjustment limit", "CAP-04", "amount",
          "Within limit / Above limit", "System-Context", 1, ""]],
        [["S-00", "Start", "The chat opens", "DEC-01", "No", ""],
         ["S-01", "DEC-01=Identified", "Account holder identified", "DEC-02", "No", ""],
         ["S-02", "DEC-01=Cannot identify", "Refused: not identified", "", "Yes", "Termination"],
         ["S-04", "DEC-02=Found", "Bill retrieved", "DEC-03", "No", ""],
         ["S-05", "DEC-02=Not issued yet", "No bill for that period yet", "", "Yes", "Fallback"],
         ["S-06", "DEC-03=Explain a charge", "Charge explained", "", "Yes", "Happy path"],
         ["S-07", "DEC-03=Request an adjustment", "Adjustment requested", "DEC-04", "No", ""],
         ["S-08", "DEC-04=Within limit", "Adjustment applied", "", "Yes", "Happy path"],
         ["S-09", "DEC-04=Above limit", "Handed to a person for approval", "", "Yes",
          "Escalation"]],
        [["Identity service", "CAP-01", "No"],
         ["Billing system", "CAP-02", "No"],
         ["Adjustment service", "CAP-04", "Yes"]])


# --------------------------------------------------------------------------- 5. real scale
def wide_chain(directory: Path, blocks: int = 4) -> Path:
    """A chain of wide blocks, at the size where the explosion is the actual problem.

    The other four files are hand-written and realistic, and none of them shows much of a
    difference -- because in each one most routes *end* rather than hand on, and it is only the
    routes that hand on which multiply. That is worth knowing: dividing a narrow agent into
    capabilities buys very little, and may cost slightly.

    This one is the shape that hurts. Each block resolves three ways that carry on and two that
    stop, which is what a real identification-then-verification-then-decision agent looks like
    once every branch is declared. Four such blocks walked whole is in the hundreds; walked block
    by block it is twenty.
    """
    capabilities, decisions, states, tools = [], [], [], []
    for n in range(1, blocks + 1):
        last = n == blocks
        capability = "CAP-%02d" % n
        entry = "S-%d0" % n
        carries_on = ["S-%d1" % n, "S-%d2" % n, "S-%d3" % n]
        stops = ["S-%d8" % n, "S-%d9" % n]
        # Each block's three continuing outcomes converge on the next block's entry.
        next_entry = "S-%d0" % (n + 1) if not last else None

        decisions.append(
            ["DEC-%d1" % n, "Resolve block %d" % n, capability, "what the user supplies",
             "First way / Second way / Third way / Refused / Abandoned", "User", 1, ""])
        if not last:
            decisions.append(
                ["DEC-%d2" % n, "Hand block %d on" % n, capability, "internal state",
                 "Ready / Held", "System-Context", 1, ""])

        states.append([entry, "Start" if n == 1 else "DEC-%d2=Ready" % (n - 1),
                       "Block %d begins" % n, "DEC-%d1" % n, "No", ""])
        for label, state in zip(("First way", "Second way", "Third way"), carries_on):
            states.append([state, "DEC-%d1=%s" % (n, label),
                           "Block %d resolved: %s" % (n, label.lower()),
                           "DEC-%d2" % n if not last else "", "No" if not last else "Yes",
                           "" if not last else "Happy path"])
        states.append([stops[0], "DEC-%d1=Refused" % n, "Block %d refused" % n, "", "Yes",
                       "Termination"])
        states.append([stops[1], "DEC-%d1=Abandoned" % n, "Block %d abandoned" % n, "", "Yes",
                       "Fallback"])
        if not last:
            states.append([next_entry, "DEC-%d2=Ready" % n, "Block %d begins" % (n + 1),
                           "DEC-%d1" % (n + 1), "No", ""])
            states.append(["S-%d7" % n, "DEC-%d2=Held" % n, "Block %d held for review" % n,
                           "", "Yes", "Escalation"])

        exits = ([next_entry, "S-%d7" % n] if not last else list(carries_on)) + stops
        capabilities.append([capability, "Block %d" % n, "Gating", entry, ", ".join(exits)])
        tools.append(["Block %d service" % n, capability, "Yes" if last else "No"])

    # De-duplicate the shared entry rows -- each is declared once by the block it opens.
    seen, unique = set(), []
    for row in states:
        if row[0] in seen:
            continue
        seen.add(row[0])
        unique.append(row)

    return build(
        directory / "5_wide_chain_scale.xlsx",
        {"Use case name": "Wide chain (%d blocks)" % blocks,
         "Business objective": "Show what capability scoping is for, at the size where it matters",
         "Agent type": "Conversational assistant", "Channel / modality": "Web chat",
         "Human handoff triggers": "Any block held for review",
         "Safety requirements": "None declared; this file exists to exercise enumeration",
         "Success criteria": "The last block resolves"},
        [["P1", "Ordinary user", "Wants the request completed", "Y"],
         ["P-ADV", "Adversarial user", "Trying to act outside the remit", ""]],
        capabilities, decisions, unique, tools)


def main() -> int:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
    directory.mkdir(parents=True, exist_ok=True)
    for builder in (disputes, travel, onboarding, broken, wide_chain):
        print(f"wrote {builder(directory)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
