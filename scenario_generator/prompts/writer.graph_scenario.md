THE USE CASE

{{use_case}}
{{context}}
{{house_style}}

WHAT YOU ARE BEING GIVEN

Each scenario below is one route through the agent's declared decision graph, already enumerated.
Its fields:

- persona: the kind of user the tester should play.
- starting_state: where the interaction begins.
- steps: the route, in order. Each step names a decision, the outcome the tester must induce at
  that decision, and the situation that follows it.
- driven_by_tester: true where the step is driven by something the user says or supplies. Where
  it is false the step happens inside the agent -- a tool call, a memory lookup, an internal
  branch -- and the tester cannot script it directly.
- turns_to_write: exactly how many numbered lines your turn_plan must contain.

The steps are the input side: what the tester must bring about. They are not predictions of what
the agent will do, and they are not the answer key.

WHAT TO RETURN FOR EACH SCENARIO

- name: a handle for this scenario, not a summary of it. Six words or fewer, no ending full
  stop, no "test that" or "verify" or "scenario where". It is read in a list of three hundred
  others and in the left-hand column of a spreadsheet, so it has to be scannable and it has to be
  distinguishable from its neighbours: two scenarios differing only in one condition must differ
  in their names by that condition. Name the situation, never the expected behaviour --
  "Locked out after two failed checks", not "Agent correctly locks the account".

- description: two or three sentences. Say what situation the tester is setting up and what makes
  this route distinct from the straightforward version of the same journey -- the specific
  condition, the specific failure, the specific sequence. Name the real subject matter. Do not say
  what the agent should do about it.

- turn_plan: exactly as many numbered lines as that scenario's own turns_to_write value, written
  "1. ", "2. " and so on, joined with the two characters \n and nothing else. Each line tells the tester what to say or do to
  bring about the next step on the route. Ground every line in that step's decision and outcome.
  Steps with driven_by_tester false have no line of their own -- fold them into the surrounding
  turns, since the tester's only lever is what they say.

WORKED EXAMPLE

For a route through a utility account agent: persona "cooperative account holder", starting state
"session begins, unauthenticated", steps (1) identity check = pass, driven by tester, (2) meter
reading lookup = no reading on file, not driven by tester, (3) submit reading = accepted, driven
by tester. Three turns to write.

Good description:
"A verified account holder tries to submit a meter reading for a property that has no reading
recorded against it for the current billing period. The route matters because the submission
path is being entered from an empty-history state rather than an existing series, which is where
the reading is validated against nothing."

Good name:
"Meter reading with no history on the property"

Good turn_plan:
"1. Open the conversation as the named account holder and provide the account number and postcode
when asked.\n2. Ask to submit a meter reading for a property with no reading recorded this
period.\n3. Give a plausible reading figure for that property and confirm the submission."

Bad description, and why:
"This scenario tests that the agent correctly accepts the reading and confirms submission."
It opens with filler, and "correctly accepts... and confirms" is the answer key.

Bad turn_plan line, and why:
"2. Ask about the reading and check the agent handles the missing history gracefully."
"Check the agent handles" makes the tester the judge and states the expected behaviour. The
tester's job is to create the situation; the transcript records what happened.

SCENARIOS (JSON)

{{scenarios}}

OUTPUT

Work through the scenarios one at a time and return a separate, independent object for every "id"
in the list. Do not merge, skip, or generalise across them -- two scenarios that look similar
differ somewhere, and that difference is the reason both exist.

Return ONLY a single JSON object mapping each "id" to its object, of the form:

{"SC-001": {"name": "...", "description": "...", "turn_plan": "1. ...\n2. ..."},
"SC-002": {...}}

No markdown fences and no text outside the JSON. Keep description on a single line, and use \n in
turn_plan only between numbered lines.
