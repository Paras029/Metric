"""Write a small, well-formed example intake (a claims-dispute chatbot) for testing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # importable from anywhere

from openpyxl import Workbook

from metric.domain import sheets


def build(path: str = "claims_intake.xlsx") -> str:
    wb = Workbook()
    wb.remove(wb.active)

    l1 = sheets.add_sheet(wb, "L1 Use Case", ["Field", "Value"], [34, 90])
    sheets.write_rows(l1, [
        ["Use case name", "Cardmember Disputes Assistant"],
        ["Business objective", "Let authenticated cardmembers dispute a transaction and file a "
                               "claim, or hand off to an agent when they cannot."],
        ["Use case rating (informational)", "High"],
        ["Agent type", "chatbot"],
        ["Channel / modality", "text chat"],
        ["Human handoff triggers", "Verification lockout, ineligible transaction, transaction not found, hardship or distress signals, explicit request"],
        ["Safety requirements", "No financial, tax or legal advice; never quote a provisional credit timeline; mask card numbers to last four; no third-party account data; route hardship to the specialist team"],
        ["Success criteria", "A dispute correctly filed, or a clean handoff carrying the context already gathered. Correct outcomes matter more than completion rate."],
    ])

    personas = sheets.add_sheet(wb, "Personas",
                                ["ID", "Name", "Applies To", "Default"], [10, 34, 28, 10])
    sheets.write_rows(personas, [
        ["P1", "Cooperative, verified cardmember", "Happy path", "Y"],
        ["P2", "Frustrated cardmember", "Escalation", ""],
    ])

    caps = sheets.add_sheet(wb, "L2 Capabilities", ["Capability ID", "Name", "Type"], [14, 30, 20])
    sheets.write_rows(caps, [
        ["CAP-01", "Authentication", "Gating"],
        ["CAP-02", "Claim retrieval", "Lookup"],
        ["CAP-03", "Dispute filing", "Transactional"],
        ["CAP-04", "Human handoff", "Advisory"],
        ["CAP-05", "Cardmember profile access", "PII-handling"],
    ])

    decisions = sheets.add_sheet(wb, "L3 Decisions",
                                 ["Decision ID", "Decision", "Triggering Capability", "Inputs",
                                  "Possible Outputs", "Input Source", "Max Attempts",
                                  "Outcome Condition"],
                                 [12, 28, 22, 30, 30, 20, 14, 34])
    sheets.write_rows(decisions, [
        ["DEC-01", "Identity verification", "CAP-01", "credentials", "Pass / Fail",
         "User", 3, "Three failed attempts and the session is locked"],
        ["DEC-02", "Transaction lookup", "CAP-02", "card last four + date", "Found / Not found",
         "User", 2, "Matched on last four digits and a date within the last 24 months"],
        ["DEC-03", "Dispute eligibility", "CAP-03", "transaction details",
         "Eligible / Not eligible", "Tool", 1,
         "Not eligible if the transaction is older than 120 days or already under dispute"],
        ["DEC-04", "Dispute filing", "CAP-03", "eligible transaction", "Filed / Failed",
         "User", 1, "Amounts over 500 are filed but routed for supervisor review"],
        ["DEC-05", "Handoff offer", "CAP-04", "customer response", "Accepted / Declined",
         "User", 1, ""],
    ])

    states = sheets.add_sheet(wb, "L4 States",
                              ["State ID", "Reached Via", "Description", "Valid Next Decisions",
                               "Terminal?", "Outcome Type"], [10, 26, 34, 26, 11, 16])
    sheets.write_rows(states, [
        ["S-00", "Start", "Session begins, unauthenticated", "DEC-01", "No", ""],
        ["S-01", "DEC-01=Pass", "Authenticated", "DEC-02", "No", ""],
        ["S-02", "DEC-01=Fail", "Verification failed, may retry", "DEC-01", "No", ""],
        ["S-03", "DEC-02=Found", "Transaction located", "DEC-03", "No", ""],
        ["S-04", "DEC-02=Not found", "No matching transaction", "DEC-05", "No", ""],
        ["S-05", "DEC-03=Eligible", "Dispute eligible", "DEC-04", "No", ""],
        ["S-06", "DEC-03=Not eligible", "Dispute not eligible", "DEC-05", "No", ""],
        ["S-07", "DEC-04=Filed", "Dispute filed and confirmed", "", "Yes", "Happy path"],
        ["S-08", "DEC-04=Failed", "Filing failed, hand off", "", "Yes", "Fallback"],
        ["S-09", "DEC-05=Accepted", "Handed off to a human agent", "", "Yes", "Escalation"],
        ["S-10", "DEC-05=Declined", "Customer declined handoff, session ends", "", "Yes",
         "Termination"],
    ])

    tools = sheets.add_sheet(wb, "Tools", ["Tool Name", "Capability ID", "State-changing?"],
                             [30, 16, 16])
    sheets.write_rows(tools, [
        ["Identity verification service", "CAP-01", "No"],
        ["Claims lookup API", "CAP-02", "No"],
        ["Dispute filing API", "CAP-03", "Yes"],
        ["Cardmember profile service", "CAP-05", "No"],
    ])

    wb.save(path)
    return path


if __name__ == "__main__":
    print("Wrote", build(sys.argv[1] if len(sys.argv) > 1 else "claims_intake.xlsx"))
