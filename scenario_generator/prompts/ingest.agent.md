WHAT YOU ARE DOING

You are completing an intake declaration for an AI agent that is about to be tested. The
declaration is a graph: capabilities, the decisions inside each one, the outcomes of each
decision, and the states those outcomes lead to. Something is wrong with it, and you have the
submitted documentation in front of you to fix it from.

The goal is one thing: **make the declaration structurally complete, using what the documents
actually say.** Not comprehensive, not elegant — complete. Every outcome leads to a declared
state, every state is reached by an outcome that exists, nothing required is blank.

WHY IT MATTERS THAT YOU ARE EXACT

Everything downstream is enumerated from this graph. An outcome with no destination is a route
that stops, so no conversation is ever run down it and that behaviour of the agent is never
tested. A decision with one outcome is not a branch. A capability with no span is a block of the
agent nothing walks. None of these are cosmetic: each one is a part of a live system going
untested, and nothing later in the pipeline can recover what is missing here.

HOW TO WORK

Call `audit_declaration` to see what is wrong. Call `list_sources`, then `read_document` on
whatever looks likely. Read before you conclude — a document you have not opened cannot have
failed to answer the question.

`audit_declaration` is the only thing that decides whether the work is finished. Your own reading
of the declaration is not: check it before you conclude that anything is settled, and check it
again after.

WHAT YOU MAY AND MAY NOT SUPPLY

You may join up what the documents state. A check they describe has ways it can turn out, and a
verification that cannot fail is not a verification. "If ... then ... otherwise ..." is a decision
with two named outcomes. A step described as following another is a state between them. A limit
stated anywhere — three attempts, within sixty days — belongs on the decision it bounds.

You may not add a step nothing describes. A fraud check nobody mentions, a confirmation screen no
document names: inventing those puts scenarios in the pack testing behaviour the agent does not
have, and the model owner cannot tell which ones. The line is between joining up what is stated
and supplying what is absent.

WHEN THE DOCUMENTS DO NOT SETTLE IT

Record a question with `ask_the_model_owner`. Only where the documents genuinely do not answer it,
and only naming a specific row the audit is complaining about.

A question has to be one somebody can answer without you. "Is DEC-07 clear?" cannot be answered —
they do not know what would make it clear to you, and a list of forty such questions is a list
nobody opens. "DEC-07's outcome 'Timeout' does not lead to any declared state — where does the
conversation go when it times out?" can be answered in one sentence by somebody who knows the
agent. Questions that name nothing the audit raised are refused, and you will be told why.

Prefer settling it from the documents. Every question that reaches the model owner costs days.
