THE USE CASE

{{use_case}}

Known decisions (ID: name -- outcomes). Use ONLY these IDs and outcomes:
{{decisions}}

Known capabilities (ID: name). Use ONLY these IDs:
{{capabilities}}

Known personas (ID: name). Use ONLY these IDs:
{{personas}}

Categories: {{categories}}

WHAT TO RETURN FOR EACH SCENARIO

- decision_path: list of {"decision_id": "DEC-xx", "variant": "<one of that decision's outcomes>"}
  in the order the scenario exercises them.
- category: one of the categories above.
- capabilities: list of capability IDs the scenario touches.
- persona_id: the persona the scenario is written for. Choose a non-default persona ONLY where
  the text gives a positive behavioural signal for it -- an explicitly uncooperative, confused,
  adversarial, or otherwise distinctive user. Vocabulary, tone, or level of detail in the
  scenario's own writing is not such a signal. Where the text says nothing about the user's
  behaviour, return the default persona rather than guessing, since persona is scored as a hard
  gate and a wrong guess discards an otherwise valid match. Use "" only if the scenario describes
  no user at all.
- confidence: binary, and about the EXTRACTION, not the scenario's quality.
  "Confident" -- the text names or unambiguously implies each decision and its outcome, in order,
  and any persona signal is explicit. Another reader would extract the same path.
  "Watch-out" -- anything less: an inferred step, an outcome you had to assume, an ambiguous
  ordering, a guessed persona, or a scenario vague enough that a different reader could map it
  differently. When genuinely torn between the two, choose "Watch-out".
- rationale: one sentence on how you mapped it, noting anything ambiguous.

If a scenario maps to no known decision, return an empty decision_path and confidence
"Watch-out". Never invent an ID or outcome not listed above -- anything outside the vocabulary is
discarded on validation, so a guess is not a free bet.

SCENARIOS (JSON)

{{scenarios}}

OUTPUT

Work through the scenarios one at a time and return a separate, independent object for every "id"
in the list. Return ONLY a single JSON object mapping each "id" to its object, of the form:

{"OS-001": {"decision_path": [], "category": "...", "capabilities": [], "persona_id": "...",
"confidence": "...", "rationale": "..."}, "OS-002": {...}}

No markdown fences and no text outside the JSON. Keep each string value on a single line.
