THE USE CASE

{{use_case}}
{{context}}
{{house_style}}

WHAT YOU ARE BEING GIVEN

Each scenario below is one route through the agent's declared decision graph, already enumerated.
Its fields:

- persona: the kind of user the tester should play.
- starting_state: where the interaction begins.
- already_established: what the tester must arrange *before* the first turn, when present. A
  scenario is scoped to one capability of the agent, so it usually does not begin at the start of
  the conversation -- one testing verification begins with the cardmember already identified.
  Where this is present, the route below covers only that capability, and the turn plan must
  start from that position. Do not write turns that re-do the earlier part of the journey: those
  turns test a different capability, and this scenario would then not test its own.
- tests_capability: which capability of the agent this scenario exercises, when scoped to one.
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
  what the agent should do about it. Where `already_established` is present, open by stating that
  position in the tester's own words, in one short clause, then say what is being tested from
  there.

- turn_plan: exactly as many numbered lines as that scenario's own turns_to_write value, written
  "1. ", "2. " and so on, joined with the two characters \n and nothing else. Each line tells the tester what to say or do to
  bring about the next step on the route. Ground every line in that step's decision and outcome.
  Steps with driven_by_tester false have no line of their own -- fold them into the surrounding
  turns, since the tester's only lever is what they say.

THE STANDARD BOTH FIELDS ARE HELD TO

The person running this has never seen the agent, has no access to its documentation, and cannot
ask you a question. What you write is the whole of what they get. So:

- **Every line names what the tester supplies, not that they supply something.** "Give the booking
  reference and the departure date" is runnable. "Provide the relevant details" is not: it leaves
  the tester to invent the test, and two testers then run two different tests whose transcripts
  cannot be compared.
- **Where a step needs a value of a particular kind, say which kind.** The condition the route
  turns on is usually a property of what the tester brings -- a reference that is not on the
  account, an amount over a limit, a date inside a restricted window. Name that property. You do
  not need a real value; you need the tester to be unable to pick a wrong one.
- **Never leave a bracket for somebody to fill in.** No "[insert reference]", no "TBD", no
  "<amount>".
- **A description is two or three sentences and says three things**: the position the tester
  starts from, the specific condition that makes this route different from the straightforward
  version of the same journey, and the real subject matter in the words a user of this service
  would use. One sentence is not a description.

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

A second bad turn_plan line, and why:
"1. Open the conversation and provide the necessary information to be identified."
Nothing here can be executed. Which information -- an account number, a postcode, a date of birth?
The line has named the *step* instead of the tester's part in it, and the route turns on exactly
what was supplied. "Open the conversation as the account holder and give the account number and
postcode when asked" is the same step, written so it can be run.

A bad description, and why:
"The account holder submits a reading."
One sentence, no condition, no subject matter beyond the bare action. Nothing in it distinguishes
this route from the ordinary one, which is the only reason this scenario exists separately.

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
