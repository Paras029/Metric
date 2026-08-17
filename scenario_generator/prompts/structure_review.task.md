WHAT THIS IS

The intake below describes an agent as a decision graph, before it is walked into a scenario space.
This is a chance to catch two kinds of problem while they are still cheap to fix: pieces of the
declaration that do not connect to anything, and pieces that connect fine but test the same thing
more than once.

WHAT YOU HAVE

{{use_case}}

THE DECLARED GRAPH

{{structure}}
{{context}}

STRUCTURAL HINTS (computed, not judged — decisions whose every outcome lands on the same place
downstream, which is what makes two decisions *candidates* for merging, not proof that they
should)

{{hints}}

TWO KINDS OF PROPOSAL

1. RECONNECTIONS — a decision or state that is declared but does not connect to the rest of the
   graph: nothing leads to it, or it leads nowhere. Say precisely where it belongs:
   - A disconnected **decision** needs a state that should lead to it. Name that state.
   - A disconnected **state** needs the decision outcome that reaches it. Name it as
     `DEC-xx=Outcome`, using the decision's own outcome wording.
   Ground every one of these in what the use case and the rest of the graph say. Leave it alone
   if you cannot tell — a wrong guess here silently redirects a real branch, which is worse than
   leaving the gap visible for a person to close.

2. CONSOLIDATIONS — two or more decisions that are alternate ways of establishing the *same* fact
   rather than genuinely different branches. The test that matters: after each of them, does the
   interaction continue exactly the same way regardless of which one was taken? If yes, they are
   candidates — separately, they multiply the scenario space by every combination without testing
   anything additional past that point; merged into one decision, with each original outcome
   folded into a new shared outcome, the same downstream behaviour is tested at a fraction of the
   cost. If no — if what happens afterwards actually differs by which method was used — do not
   propose merging them, because that difference is exactly what a scenario space exists to catch.

   Do not propose merging decisions that differ in *what* they gate rather than *how* they
   establish it: authenticating a caller and authorising a specific transaction are different
   checks even where both happen to produce a Pass/Fail outcome.

   A capability of type Gating or PII-handling, or a decision central to the use case's stated
   objective, should weigh toward *not* merging even where the structural test above is satisfied
   — note this in the rationale rather than silently declining to propose it, so a person can see
   the tradeoff you weighed and decide for themselves.

   **Never merge decisions belonging to different capabilities.** A capability is a block of the
   agent with its own entry and exit states, and the scenario space is enumerated one block at a
   time. Two decisions in different blocks may look like alternate routes to the same fact and
   still be no such thing: merging them welds two blocks together and changes how the whole space
   is enumerated, which is a far larger change than the one being proposed. Where two decisions
   across a boundary really are the same check, say so in the rationale of some other proposal and
   leave the merge to a person.

   Every proposed merge must keep every capability the merged decisions used, listed against the
   new decision — consolidating the branch is not the same as forgetting what it touches, even
   though only one of them can remain the workbook's Triggering Capability.

   `outcome_map` must cover every `DECID=OldOutcome` pair across every decision being merged,
   mapping each to one of the new `outcomes`. A pair a person would expect to see and does not
   find here is a pair nothing will be able to rewire.

WHAT TO RETURN

{"reconnections": [
   {"kind": "decision", "id": "DEC-09", "attach_to_state": "S-04", "rationale": "..."},
   {"kind": "state", "id": "S-07", "reached_via": "DEC-06=Escalate", "rationale": "..."}
 ],
 "consolidations": [
   {"decisions": ["DEC-03", "DEC-05"], "id": "DEC-03", "name": "Identify caller",
    "outcomes": ["Identified", "Not identified"],
    "outcome_map": {"DEC-03=Matched": "Identified", "DEC-05=Matched": "Identified",
                    "DEC-03=Not matched": "Not identified", "DEC-05=Not matched": "Not identified"},
    "capabilities": ["CAP-02", "CAP-04"], "importance": "Low",
    "rationale": "..."}
 ]}

Either list may be empty — most intakes need no reconnections, and not every intake has a
consolidation worth proposing. Only propose what you can ground in the graph above.

No markdown fences and no text outside the JSON. Keep every string value on a single line.
