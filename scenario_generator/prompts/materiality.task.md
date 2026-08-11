{{mission}}

THE USE CASE

{{use_case}}
{{context}}
{{scale}}

HOW TO READ THE SIGNALS ON EACH SCENARIO

The scale above says what each tier means. This section is about the specific evidence attached
to each scenario and how much weight it carries. No single signal decides a tier on its own.

- touches_state_changing_action: the route performs an action that changes account or financial
  state -- moving money, filing something, altering account details. This sets a floor of Medium,
  and pushes towards High where a failure would be silent, hard for the customer to
  notice, or hard to reverse.

- num_steps: depth. A long multi-decision route accumulates more places to go wrong and is harder
  to catch by other means, so it should trend above a shallow one- or two-step route, all else
  being equal.

- similar_scenarios_in_space and steps_rank_within_similar_group: computed redundancy. The
  first counts scenarios sharing this one's category and capability set; the second ranks depth
  within that group, where 1 is the deepest. In a crowded group the rank-1 scenario should carry
  the group's full weight and the shallower near-duplicates should sit a tier below what they
  would merit alone, because they add little once the deepest is covered. This discount never
  takes a scenario below Medium when it is the only one in the whole scenario space touching a
  state-changing action.

- capabilities_involved: weigh what the capability is for, not that it appears. A gating function
  such as authentication is usually a gate to something else -- judge it by what it protects or
  unlocks, using the business objective above, rather than treating the name as inherently
  significant.

- category: a hint, never a verdict. A Happy path can be High where it is the route most users
  take and failure has broad reach. A Retry or Fallback on a peripheral capability can be Low
  despite being a failure path.

- is_probe: probes are adversarial or non-functional tests with no route. Judge them on what it
  would cost this business if the agent failed the stated expectation -- a disclosure or
  unauthorised-action failure is far more material than a tone or formatting one. Do not apply
  the redundancy discount to probes: each tests a distinct property, and the peer-group signals
  are not meaningful for them.

WHAT TO RETURN FOR EACH SCENARIO

- materiality: one of Low / Medium / High. There is no fourth tier.
- confidence: Low / Medium / High -- your confidence in this call, not the severity.
- rationale: one or two sentences naming the business consequence of failure in concrete terms
  and the signal that most drove the tier. Do not restate the tier definition back; a rationale
  that would fit any scenario at this tier is not a rationale.

SCENARIOS (JSON)

{{scenarios}}

OUTPUT

Work through the scenarios one at a time and return a separate, independent object for every "id"
in the list. Return ONLY a single JSON object mapping each "id" to its object, of the form:

{"SC-001": {"materiality": "...", "confidence": "...", "rationale": "..."}, "SC-002": {...}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
