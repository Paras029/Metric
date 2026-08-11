WHAT YOU ARE DOING

A model owner has sent the conversations they already ran against their agent, in whatever shape
their logging system exports. It is almost certainly not the shape this tool asked for. Your job
is to say **which column is which**, so the file can be read as conversations without anybody
having to reformat it by hand.

You are not reading the conversations. You are reading the *header and a few rows* and naming the
columns. Everything else is done in code from what you return, over every row in the file.

WHAT THE TOOL ASKS FOR

The template this tool issues has one row per scenario, variation and turn, with the user's words
and the agent's words in two columns of the same row. Almost nothing exports that way. Two other
shapes are common and both are fine:

- **one row per turn** — a conversation id repeated down the sheet, one row per utterance, and a
  column saying who was speaking.
- **one row per conversation** — the whole exchange in a single cell, usually with `User:` and
  `Agent:` markers inside it.

WHAT YOU HAVE

The file is `{{origin}}`. Its header row, and the first rows under it:

{{sample}}

HOW TO DECIDE

**Which shape is it?** If one identifier repeats down the sheet and there is a column naming a
speaker, it is one row per turn. If each row stands alone with a long block of text, it is one row
per conversation. If both halves of an exchange sit in two columns of one row — a "user said" and
an "agent said" — that is one row per turn as well; name both columns and leave `speaker` empty.

**Which column carries the words?** The one with the sentences in it. A column of ids, timestamps,
durations, scores or yes/no flags is not it, however it is headed.

**Is anything already labelled?** Teams often carry their own name for what they were testing —
"scenario", "intent", "test case", "use case". Name that column in `group`. It is read and
assessed, never trusted. Separately, if a column holds ids that look like this tool's own
(`SC-001`, `NF-014`), name it in `scenario_id` instead: that is the file saying which of our
scenarios it was run against, which is a different and much stronger thing.

**Say what you could not find.** A missing column is a fact worth returning. Do not invent a
column name that is not in the header.

WHAT TO RETURN

A JSON object with exactly these keys. Use the header text **exactly as it appears** in the file,
or an empty string where the file has no such column.

- `layout`: `"turn_per_row"` or `"conversation_per_row"`.
- `conversation_id`: the column identifying which conversation a row belongs to.
- `speaker`: the column saying who was talking. Empty for one row per conversation, and empty
  where the two sides are in two columns instead.
- `user_values`: the values in that column that mean the customer, exactly as the file spells
  them, as a list. Real exports rarely say "User" — `C`, `CUST`, `1`, `inbound` are all common,
  and a code nobody translates leaves every utterance filed under a speaker that means nothing.
- `agent_values`: the same for the values meaning the agent.
- `text`: the column carrying the words. Empty where `user_text` and `agent_text` are used.
- `user_text`: the column carrying what the user said, where the two sides are in separate columns.
- `agent_text`: the column carrying what the agent said, in the same case.
- `scenario_id`: a column holding this tool's own scenario ids, or empty.
- `group`: the column holding the team's own label for what they were testing, or empty.
- `note`: one sentence saying how you read the file, for a person who has to defend the figure
  that comes out of it. Name what you were unsure of.

OUTPUT

Return ONLY that JSON object. No markdown fences and no text outside it.
