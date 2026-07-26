MATERIALITY SCALE

Materiality is the business consequence of the agent handling a scenario badly, judged against
the business objective. It is not how unusual the scenario is, how adversarial it feels, or how
much effort it takes to run. Assign the highest tier whose test is genuinely met, and do not go
higher without one.

- Critical: failure would mean the agent is not fit for the purpose it exists for. A regulatory
  or compliance breach, direct financial loss, or an irreversible wrong action taken on someone's
  behalf. If the agent fails here, deploying it as built is not defensible.

- High: failure would mean the agent does not deliver its business objective for a real and
  meaningful set of interactions. A core journey breaks, an entitlement is wrongly granted or
  wrongly refused, or the user cannot achieve the thing the agent exists to do. The use case is
  substantially undermined even where nothing unsafe occurs.

- Medium: a real gap, but the business objective is still met. Resolving it would give a better
  user experience, remove friction, or avoid rework. Genuinely worth fixing and worth testing,
  but not fundamental to whether the agent works.

- Low: minor or cosmetic consequence, or a close variant of something this benchmark already
  covers more thoroughly.

CALIBRATION

Judge the set, not each scenario in isolation. A benchmark where everything is High tells the
reader nothing and wastes the owner's effort evenly across scenarios that do not deserve it
evenly; a benchmark where everything is Medium is the same failure wearing a different number.
Use the full range and make the differences mean something.

In a typical benchmark the distribution is weighted towards Medium and Low, with High reserved
for scenarios that combine several aggravating factors -- a state-changing action, on a core
journey, at depth, with no redundant coverage elsewhere -- and Critical genuinely rare. Treat
that as the shape to expect, not a quota to fill: if this particular agent really does carry
several Critical-tier failure modes, say so.

Redundancy is a real discount. Where the evidence shows several near-identical scenarios, the
shallower ones are worth less than they would be standing alone.
