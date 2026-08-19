"""C. The same two lists, pruned to the states that could plausibly be a boundary.

The smallest change of the three: keep the control exactly as it is and stop showing states that
cannot be an entry or an exit of this capability. An entry candidate is a state that offers one
of the capability's own decisions; an exit candidate is a state one of them routes to. On the
example that takes 84 checkboxes to 18, and it needs no new interaction and no derivation.

Worth having on the table because it is an afternoon's work, and because it is what B falls back
to when a validator overrides the derivation.
"""
from _shell import page

CSS = """
.cap { border: 1px solid var(--rule); border-radius: var(--r-md); padding: var(--s4);
       margin-bottom: var(--s4); background: var(--surface); }
.cap__row { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap; }
.cap__name { font-weight: 650; }
.grow { margin-left: auto; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s5); margin-top: var(--s3);
        padding-top: var(--s3); border-top: 1px solid var(--rule-soft); }
.side__label { font-size: var(--t-micro); font-weight: 700; letter-spacing: .1em;
               text-transform: uppercase; color: var(--ink-dim); margin: 0 0 var(--s2); }
.rows { list-style: none; margin: 0; padding: 0; }
.rows li { padding: .22rem 0; }
.why { font-size: var(--t-meta); color: var(--ink-dim); margin: var(--s2) 0 0; }
.more { border: 0; background: none; color: var(--blue-dark); cursor: pointer; font: inherit;
        font-size: var(--t-meta); text-decoration: underline; padding: .3rem 0 0; }
.foot { display: flex; align-items: center; gap: var(--s3); margin-top: var(--s4);
        padding-top: var(--s3); border-top: 1px solid var(--rule-soft); }
.foot span { font-size: var(--t-meta); color: var(--ink-dim); }
"""


def _rows(name, options):
    out = ""
    for sid, text, checked, ends in options:
        mark = ' <span class="mark">ends</span>' if ends else ""
        out += (f'<li><label class="check"><input type="checkbox" name="{name}"'
                f'{" checked" if checked else ""}>'
                f'<span class="mono">{sid}</span> {text}{mark}</label></li>')
    return f'<ul class="rows">{out}</ul>'


def build() -> str:
    return page("C — the same lists, pruned to candidates", """
      <div class="sheet">
        <h1>Capabilities</h1>
        <p class="lede">C &mdash; the same control, showing only states that could be a
          boundary of this capability. 84 checkboxes down to 18.</p>

        <div class="cap">
          <div class="cap__row">
            <span class="mono">CAP-01</span>
            <span class="cap__name">Identification</span>
            <span class="mark">Gating</span>
            <span class="grow"><button class="button button--primary">Save span</button></span>
          </div>
          <div class="cols">
            <div>
              <p class="side__label">Entered at</p>
              """ + _rows("entry", [
                  ("S-00", "The chat opens", True, False),
                  ("S-01", "Card number taken, checking it", False, False),
                  ("S-02", "Card not recognised, asked again", False, False)]) + """
              <p class="why">States that offer DEC-01 or DEC-02.</p>
              <button class="more">Show all 14 states</button>
            </div>
            <div>
              <p class="side__label">Hands on or ends at</p>
              """ + _rows("exit", [
                  ("S-03", "Identified from card details", True, False),
                  ("S-04", "Identified by one-time code", True, False),
                  ("S-05", "Not identified; chat ended", True, True)]) + """
              <p class="why">States DEC-01 or DEC-02 route to.</p>
              <button class="more">Show all 14 states</button>
            </div>
          </div>
        </div>

        <div class="cap">
          <div class="cap__row">
            <span class="mono">CAP-02</span>
            <span class="cap__name">Verification</span>
            <span class="mark">Gating</span>
            <span class="grow"><button class="button button--primary">Save span</button></span>
          </div>
          <div class="cols">
            <div>
              <p class="side__label">Entered at</p>
              """ + _rows("entry", [
                  ("S-03", "Identified from card details", True, False),
                  ("S-04", "Identified by one-time code", True, False),
                  ("S-06", "Security question asked", False, False),
                  ("S-07", "Answer wrong, one try left", False, False)]) + """
              <p class="why">States that offer DEC-03, DEC-04 or DEC-05.</p>
            </div>
            <div>
              <p class="side__label">Hands on or ends at</p>
              """ + _rows("exit", [
                  ("S-06", "Security question asked", False, False),
                  ("S-07", "Answer wrong, one try left", False, False),
                  ("S-08", "Verified cardmember on a clear account", True, False),
                  ("S-09", "Verification failed; passed to an adviser", True, True)]) + """
              <p class="why">States DEC-03, DEC-04 or DEC-05 route to.</p>
            </div>
          </div>
          <div class="foot">
            <span>Entered two ways, so it is walked twice. <b>6 scenarios.</b></span>
          </div>
        </div>
      </div>
      <p class="legend"><b>How it reads.</b> Nothing about the interaction changes &mdash; the
      same checkboxes, in the same two columns, in the same place. What changes is that the lists
      are short enough to read, each says on what basis it was pruned, and the full list is one
      click away for the case where a boundary genuinely sits outside the capability's own
      decisions. <b>What it costs:</b> two set comprehensions and a template change; no new
      interaction, no derivation, nothing to get wrong.</p>""", CSS)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    Path(sys.argv[1]).write_text(build(), encoding="utf-8")
