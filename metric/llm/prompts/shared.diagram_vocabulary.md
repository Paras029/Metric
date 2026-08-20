THE VOCABULARY TO WRITE IT IN

**Capabilities** — each distinct thing the agent can do. `CAP-01`, `CAP-02`, … Give each a type
where the diagram makes it plain: `Lookup` (reads and reports), `Transactional` (changes stored
state), `Gating` (decides whether something else may proceed — authentication is the usual one),
`Advisory` (recommends), `PII-handling` (touches personal data). Leave the type empty rather than
guessing; it decides which adversarial probes get applied.

**Decisions** — every branch point. `DEC-01`, `DEC-02`, … Each carries:
- `name`: the box's own label, in the diagram's words. If a box says "Auth check", write "Auth
  check", not "identity verification" — the rest of the documentation will use the diagram's own
  words, and a tidied label stops the two matching up.
- `outcomes`: the arrow labels leaving it, as written. **This is the most important field in the
  whole structure** — a branch whose outcomes are not named cannot be enumerated, so nothing on it
  is ever tested. Never invent an outcome name: a wrong one becomes a test of something that does
  not exist. An unlabelled arrow whose meaning you cannot read goes in `unresolved`.
- `capability_id`: which capability it belongs to, where the diagram groups them.
- `input_source`: `User`, `Tool`, `Memory-Session`, `Memory-CrossSession`, `System-Context` or
  `Document`. Only `User` steps become conversational turns, so this is what says whether a tester
  can drive the step or whether it happens inside the agent. Default to `User`.
- `max_attempts`: how many times it may be retried. A loop drawn back to the same box is a retry —
  if the diagram says how many, use that; if it shows a loop without a number, use 1 and note it
  in `unresolved`.
- `outcome_condition`: the rule or threshold selecting between outcomes, only where one is drawn.

**States** — every position the interaction can be in. `S-00`, `S-01`, … Each carries:
- `reached_via`: `Start` for the opening state, otherwise exactly `DEC-xx=Outcome`, naming the
  decision and the outcome that leads here. **This is what connects the graph.** Every outcome you
  declared on a decision should appear here on some state.
- `description`: what the box says, or what the position is.
- `next_decisions`: the decisions reachable from here. Empty where the interaction ends.
- `is_terminal`: whether the flow stops here — a hand-off to a person, a rejection, a completed
  outcome. **Do not mark a state terminal because its next step is unclear.** A box whose outgoing
  arrow you could not follow is a gap to name in `unresolved`, not an ending. The two look
  identical once written down, only one is true, and nothing later re-derives this from the
  picture.
- `outcome_type`, on terminal states only: `Happy path`, `Retry`, `Fallback`, `Escalation` or
  `Termination`.
