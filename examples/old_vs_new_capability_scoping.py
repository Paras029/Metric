"""Old vs new, on identification -> verification -> charge verification.

Run it and read both listings; the difference is the point, and it is not the count.

    python examples/old_vs_new_capability_scoping.py

**On this graph the count does not change: thirteen scenarios either way.** That is worth
understanding before reading any number as the benefit.

Walked whole, eight of the thirteen are dispute-filing scenarios that differ only in how the
cardmember happened to be identified four steps earlier -- and identification itself is never
tested as a thing, because no route ends there. Walked per block, identification gets three
scenarios of its own, verification six, charge verification four, and the charge logic is
exercised four times instead of eight.

The count stays level here because verification can be entered two ways and is therefore walked
twice, which cancels the saving on a chain this short. What does not cancel is depth: the saving
multiplies with each further block while the doubling stays a doubling. On a four-block chain five
outcomes wide it is 200 routes against 29, and at five blocks 605 against 37 -- see
examples/intakes/CHECKS.md.

If verification genuinely behaves the same however the cardmember was identified, declaring one
entry state instead of two collapses those six scenarios to three. That is a modelling decision
about the agent, which is why nothing here makes it automatically.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenario_generator.core.graph import DecisionGraph, enumerate_by_span
from scenario_generator.core.generation import instantiate_span, number_scenarios
from scenario_generator.core.models import Capability, Decision, Persona, State

# Each block has real internal depth, as a real one does: identification can succeed two ways or
# fail; verification asks two questions; charge verification classifies then checks the window.
DECISIONS = [
    Decision("DEC-01", "Identify the cardmember", "CAP-01", "card last four",
             ["By card details", "By one-time code", "Cannot identify"]),
    Decision("DEC-02", "Confirm recent activity", "CAP-02", "recent charge recall",
             ["Recalled", "Not recalled"]),
    Decision("DEC-03", "Check account standing", "CAP-02", "account flags",
             ["Clear", "Restricted"]),
    Decision("DEC-04", "Classify the dispute", "CAP-03", "reason given",
             ["Unauthorised", "Service issue"]),
    Decision("DEC-05", "Check the dispute window", "CAP-03", "charge date",
             ["Within window", "Outside window"]),
]
STATES = [
    State("S-00", "Start", "The chat opens", ["DEC-01"], False),
    State("S-01", "DEC-01=By card details", "Identified from card details", ["DEC-02"], False),
    State("S-02", "DEC-01=By one-time code", "Identified by one-time code", ["DEC-02"], False),
    State("S-03", "DEC-01=Cannot identify", "Locked out", [], True, "Termination"),
    State("S-04", "DEC-02=Recalled", "Recent activity confirmed", ["DEC-03"], False),
    State("S-05", "DEC-02=Not recalled", "Could not confirm activity", [], True, "Escalation"),
    State("S-06", "DEC-03=Clear", "Verified on a clear account", ["DEC-04"], False),
    State("S-07", "DEC-03=Restricted", "Account restricted, sent to fraud", [], True, "Escalation"),
    State("S-08", "DEC-04=Unauthorised", "Treated as an unauthorised charge", ["DEC-05"], False),
    State("S-09", "DEC-04=Service issue", "Treated as a service dispute", ["DEC-05"], False),
    State("S-10", "DEC-05=Within window", "Dispute filed", [], True, "Happy path"),
    State("S-11", "DEC-05=Outside window", "Refused, outside the window", [], True, "Fallback"),
]
CAPABILITIES = [
    Capability("CAP-01", "Identification", "Gating",
               entry_states=("S-00",), exit_states=("S-01", "S-02", "S-03")),
    Capability("CAP-02", "Verification", "Gating",
               entry_states=("S-01", "S-02"), exit_states=("S-06", "S-05", "S-07")),
    Capability("CAP-03", "Charge verification", "Transactional",
               entry_states=("S-06",), exit_states=("S-10", "S-11")),
]
PERSONAS = [Persona("P1", "Cardmember", ["Wants a charge investigated"], True)]

graph = DecisionGraph(DECISIONS, STATES)
NAME = {s.id: s.description for s in STATES}


def label(path):
    return " → ".join(f"{s.decision_id}={s.variant}" for s in path)


print("=" * 78)
print("BEFORE — one walk, start to finish")
print("=" * 78)
old = []
for span, walked, aug in enumerate_by_span(graph, []):
    old += instantiate_span(span, walked, aug, graph, PERSONAS, [])
number_scenarios(old)
for s in old:
    print(f"  {s.id}  {label(s.path)}")
    print(f"          starts: {s.seeded_state:<34} ends: {NAME.get(s.path[-1].next_state, '?')}")
print(f"\n  {len(old)} scenarios. Every one runs the whole journey.")

print()
print("=" * 78)
print("AFTER — one walk per capability")
print("=" * 78)
new = []
for span, walked, aug in enumerate_by_span(graph, CAPABILITIES):
    new += instantiate_span(span, walked, aug, graph, PERSONAS, [])
number_scenarios(new)
block = None
for s in new:
    if s.capability_id != block:
        block = s.capability_id
        name = next(c.name for c in CAPABILITIES if c.id == block)
        print(f"\n  ── {block}  {name} " + "─" * (48 - len(name)))
    print(f"  {s.id}  {label(s.path)}")
    print(f"          precondition: {s.precondition}")
    print(f"          ends: {NAME.get(s.path[-1].next_state, '?')}")
print(f"\n  {len(new)} scenarios, in three groups.")
