CAPABILITY, DECISION, STATE

Three things, and they are not interchangeable. Everything downstream is enumerated from the
difference between them, so getting it wrong does not produce a slightly worse declaration — it
produces scenarios that test something the agent does not do, or none at all for something it does.

**A decision is atomic.** One point where the agent chooses, with named outcomes, and nothing
inside it left to describe. "Check whether the last four digits match" is a decision: it resolves
one way or another and there is nothing smaller to say about it. Each outcome routes to exactly
one state. A decision is the only thing that branches, and a branch whose outcomes are not named
cannot be enumerated, so nothing on it is ever tested.

**A state is where an outcome lands.** The position the interaction is in after one decision has
resolved one way — "identified from card details", "locked out", "dispute filed". A state is not
an action and not a step; it is the situation between steps. It records which outcome reached it
(`DEC-04=Pass`) and which decisions can be taken from it next. A state where nothing follows and
the interaction stops is terminal, and terminal states are what routes are allowed to end at.

**A capability is a group of decisions.** A whole piece of the agent's work, named for what it
accomplishes rather than for how: "identification", "verification", "charge handling". It is a
high-level idea, and its internal workings may not be spelled out anywhere — that is normal and
does not make it any less real. A capability is not a branch and has no outcomes of its own; what
it has is the decisions inside it and the states those decisions route between.

The relationship, stated once:

    capability  =  a set of decisions, and the states they route between
    decision    =  one atomic choice, with named outcomes
    state       =  where one outcome of one decision lands
    outcome     =  the label on the route from a decision to a state

So: capabilities *contain* decisions. Decisions *produce* states. States *offer* decisions. A
capability never routes to another capability directly — a decision inside it routes to a state,
and that state is where the next capability begins.

TELLING THEM APART WHEN THE DOCUMENTATION BLURS THEM

Documentation frequently describes an agent at capability level and nothing finer: "the agent
identifies the customer, verifies their account, then handles the request". Three capabilities,
and not one decision among them — "identifies the customer" is a whole piece of work, not an
atomic choice, and how it can turn out is not stated.

Two tests separate them.

*Can you name its outcomes, from the documentation?* A decision has named ways it resolves, and
they come from the documents rather than from you. If you find yourself inventing "success" and
"failure" because a step must presumably have them, you are looking at a capability described in
one line, not at a decision.

*Is there anything smaller to say about it?* If the answer is "well, it does several things", it
is a capability. If the answer is "it either matches or it does not", it is a decision.

WHAT TO DO WITH CAPABILITY-LEVEL DOCUMENTATION

Declare the capabilities. Do **not** manufacture atomic decisions to fill them out. An invented
decision with invented outcomes becomes scenarios asking a model owner to test behaviour the agent
may not have, and nothing downstream can tell those from the real ones — which is worse than a
declaration that is honestly thin, because a thin one can be asked about and a wrong one cannot.

Where a capability is described but its decisions are not, say so: declare the capability, leave
its decisions undeclared, and record a question naming that capability and asking what the agent
actually decides inside it. That question is answerable in one conversation with whoever owns the
agent. Fabricated decisions are not recoverable at all.
