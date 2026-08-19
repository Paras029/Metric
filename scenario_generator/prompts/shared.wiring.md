HOW THE GRAPH IS WIRED, AND WHAT EACH COLUMN IS FOR

The declaration is not a form to be filled in field by field. It is a graph, and two columns are
the only things that connect it:

- **A state's `reached_via`** says which decision outcome arrives at this state.
- **A state's `next_decisions`** says which decisions leave this state.

Every route the validation team will ever test is walked along those two columns and nothing
else. A decision no state names in `next_decisions` is never reached by any walk; a state no
outcome names in `reached_via` is never arrived at by any walk. Both are dropped out of the
graph entirely and shown to the validator as disconnected fragments sitting below the drawing.
Getting these two columns right matters more than every descriptive field combined, because a
beautifully described decision that nothing routes into tests nothing at all.

THE TWO RULES

1. **Every decision must be reachable.** Some state must name it in `next_decisions` — except the
   very first decision, which is named by the opening state.
2. **Every state must be arrived at.** Its `reached_via` is either the literal word `Start` for
   the opening state, or `DEC-xx=Outcome` naming a decision and one of that decision's *own*
   declared outcomes, spelled exactly as that decision spells it.

Rule 2 is where this most often goes wrong: `reached_via` is written as a description of the
situation ("after the customer answers"), or names an outcome the decision does not declare, or
is left blank. Any of those and the state is unreachable. It must be an id and an outcome.

Work the two rules as a pair. For each decision, ask both questions and write both answers down:

    "How is DEC-04 arrived at?"   -> some state's next_decisions must contain DEC-04
    "Where does each of DEC-04's outcomes go?" -> some state's reached_via must name DEC-04=<that outcome>

If a decision has three outcomes, three states must between them name those three outcomes in
their `reached_via`. Two of them may be the same state — outcomes converge — but no outcome may
be unaccounted for.

A WORKED FRAGMENT

A cardmember opens a chat, the agent asks for the last four digits of the card, and either
identifies them, asks again, or gives up after three tries.

| State | reached_via | next_decisions | is_terminal |
|---|---|---|---|
| S-00 | `Start` | `DEC-01` | No |
| S-01 | `DEC-01=Identified` | `DEC-02` | No |
| S-02 | `DEC-01=Not identified` | `DEC-01` | No |
| S-03 | `DEC-01=Attempts exhausted` | *(empty)* | Yes |

| Decision | outcomes | input_source | max_attempts |
|---|---|---|---|
| DEC-01 | `Identified / Not identified / Attempts exhausted` | `User` | 3 |
| DEC-02 | ... | ... | ... |

Read what the wiring buys. `S-00` names `DEC-01`, so the walk starts by running it. Each of
`DEC-01`'s three outcomes is claimed by a state, so all three routes exist. `S-02` names `DEC-01`
again, which is what makes the retry loop a real route rather than a sentence about one, and
`max_attempts` of 3 is what stops it looping forever. `S-03` names nothing next and is terminal,
so the route ends there.

Now read what breaking it costs. Delete `DEC-02` from `S-01`'s `next_decisions` and everything
after identification vanishes from the scenario space, however carefully it is described
elsewhere. Write `S-02`'s `reached_via` as "the customer got it wrong" instead of
`DEC-01=Not identified` and the retry is never tested.

WHAT EACH COLUMN IS FOR

On a **decision**:

- `outcomes` (Possible Outputs) — every named way this branch can resolve, in the documentation's
  own words, separated by `/`. **Two at the minimum**: a check that can pass can fail, and a
  lookup that can find something can fail to find it. These are the branch labels the routes are
  named after, and they are what `reached_via` points back at, so their wording is load-bearing.
  *Example:* `Eligible / Not eligible / Referred to an adviser`
- `input_source` — where this decision gets what it decides on: `User`, `Tool`, `Memory-Session`,
  `Memory-CrossSession`, `System-Context` or `Document`. **Only `User` becomes a conversational
  turn a tester can script.** A decision the agent makes from a tool result or from context still
  belongs in the graph and still branches the routes, but nobody types anything to drive it, and
  marking it `User` puts a turn in the test script that a tester cannot perform.
  *Example:* a postcode check against the file is `Tool`; asking the cardmember which charge they
  dispute is `User`; applying a stored risk band from an earlier session is `Memory-CrossSession`.
- `capability_id` — the capability this decision belongs to. Blank where it genuinely belongs to
  none; that is a legitimate answer and the graph handles it.
- `inputs` — what is consulted, in plain words. Descriptive, not used for wiring.
- `max_attempts` — how many times this one decision may fire within a single route. `1` unless
  the evidence describes a retry; this is the only thing bounding a loop.
- `outcome_condition` — the stated threshold or rule that selects between outcomes, where one is
  given. Recorded so boundary inputs can be derived later.

On a **state**:

- `reached_via` — as above. `Start`, or `DEC-xx=Outcome`, or several of those comma-separated
  where routes converge: `DEC-02=Too old, DEC-05=Withdrawn`.
- `next_decisions` — the decisions available from here, by id. Empty **only** where the
  interaction ends here.
- `description` — what has happened and what the agent has just said or asked. This is what a
  tester reads to know what situation they are setting up, so write the position, not a label.
  *Example:* "Identity confirmed; the agent has asked which charge is disputed."
- `is_terminal` — whether the interaction ends here. Not a way of saying the evidence went quiet:
  a state whose next step is unknown is an intermediate state with a gap to flag, and calling it
  terminal invents an ending the agent does not have.
- `outcome_type` — on terminal states only: `Happy path`, `Retry`, `Fallback`, `Escalation` or
  `Termination`. This sets the category of every scenario that ends here.

WHEN THE EVIDENCE DOES NOT SAY

Declare the outcome anyway and say where it leads as best the flow allows, then flag it in the
review notes. A named branch with a destination somebody can correct in ten seconds is worth far
more than an honest blank, because the blank is not visible as a decision anybody made — it looks
like the agent simply does not do that, and nothing is ever tested there.
