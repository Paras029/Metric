WHAT EACH FIELD MEANS

- id: SC-xxx derives from the decision graph, NF-xxx is a probe, LP-xxx was added by an earlier
  review.
- origin: "graph" (a walked route), "variant-gap" (a focused route covering a declared outcome
  the walk missed), "probe" (from the probe library), "llm-proposed" (added by a review).
- decision_path: the expected sequence of (decision = outcome) pairs. An expectation of the
  correct route, not a prediction -- a real agent may reach the same outcome another way, and
  judging that belongs to a later stage, not to this review. Empty for probes.
- category: for graph scenarios, the declared outcome type of the state the route ends in. For
  probes, the probe family.
- description: what the model owner is told to test. Issued to the model owner, so it must never
  reveal the expected outcome -- that is what is being scored.
- turn_plan: the script the tester follows. Also issued.
- expected_outcome: ground truth. The terminal state for a graph scenario, the behavioural
  assertion for a probe. Never issued.
- capabilities, touches_state_changing_action: what the scenario exercises.
- tests_capability, already_established: where these are present the scenario covers one block of
  the agent rather than a whole journey, and begins with the position in `already_established`
  already arranged. Two scenarios covering the same block from *different* entry positions are not
  redundant: what they test is whether that block behaves the same however it was reached, which
  is a claim worth one scenario each. Two covering it from the same position may well be.
- num_steps, num_turns: how deep the scenario runs. A scoped scenario is short by construction;
  shortness is not thinness.
- similar_scenarios_in_space: how many scenarios share this one's block, entry position and
  category. Computed, not estimated -- treat it as evidence of redundancy.
- steps_rank_within_similar_group: depth rank within that group, 1 being deepest. A shallow
  scenario in a crowded group is usually the weakest thing in the scenario space.
- existing_materiality: the tier currently recorded. It may come from an earlier assessment pass,
  or it may still be an untouched default where that pass was not run. Treat it as one reader's
  opinion that you are free to depart from, not as a starting point to justify moving away from.
  Reach your own tier from the evidence first, then compare.
