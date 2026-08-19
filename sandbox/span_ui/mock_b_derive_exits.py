"""B. Name where the capability starts. The tool works out where it ends.

The exits are not really a second judgement. Given an entry state and the decisions the
capability owns, the states a walk leaves the block at are computable: follow the outcomes, and
wherever the walk arrives at a state that offers no decision of this capability, that is an exit.
The present control asks for the answer to a question it can already answer.

So this asks one question per capability, states the derived answer in full, and keeps an
override for the case the derivation gets wrong -- which is a real case, and is why the exits are
shown rather than hidden.
"""
from _shell import page

CSS = """
.cap { border: 1px solid var(--rule); border-radius: var(--r-md); padding: var(--s4);
       margin-bottom: var(--s4); background: var(--surface); }
.cap--open { border-color: var(--blue-edge); background: var(--blue-tint); }
.cap__row { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap; }
.cap__name { font-weight: 650; }
.grow { margin-left: auto; }
.flow { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap;
        margin-top: var(--s3); }
.chip { display: inline-flex; align-items: center; gap: .35rem; padding: .22rem .5rem;
        border-radius: var(--r-sm); font-size: var(--t-small); border: 1px solid; }
.chip--in { background: var(--surface); border-color: var(--blue); color: var(--blue-dark);
            font-weight: 600; }
.chip--out { background: var(--surface); border-color: var(--rule-strong); color: var(--ink-mid);
             border-style: dashed; }
.chip--end { background: var(--ok-tint); border-color: var(--ok); color: var(--ok); }
.chip .mono { font-weight: 700; }
.arrow { color: var(--ink-dim); font-size: var(--t-small); }
.choose { display: flex; align-items: center; gap: var(--s3); margin-top: var(--s3);
          flex-wrap: wrap; }
select { font: inherit; font-size: var(--t-small); padding: .3rem .5rem; border-radius: var(--r-sm);
         border: 1px solid var(--rule-strong); background: var(--surface); color: var(--ink);
         min-width: 22rem; }
.derived { font-size: var(--t-meta); color: var(--ink-dim); margin: var(--s3) 0 0;
           padding-top: var(--s3); border-top: 1px solid var(--rule-soft); }
.derived b { color: var(--ink); font-weight: 650; }
.link { border: 0; background: none; color: var(--blue-dark); cursor: pointer;
        font: inherit; font-size: var(--t-meta); text-decoration: underline; padding: 0; }
.side__label { font-size: var(--t-micro); font-weight: 700; letter-spacing: .1em;
               text-transform: uppercase; color: var(--ink-dim); margin: 0 0 var(--s2); }
.warnrow { margin-top: var(--s3); padding: var(--s2) var(--s3); background: var(--warn-tint);
           border: 1px solid #E8D3AC; border-radius: var(--r-sm); font-size: var(--t-meta);
           color: var(--warn); }
"""


def _chip(sid, text, kind):
    mark = ' <span class="mark">ends</span>' if kind == "end" else ""
    return (f'<span class="chip chip--{kind}"><span class="mono">{sid}</span> {text}'
            f'{mark}</span>')


def build() -> str:
    return page("B — name the entry, derive the exits", """
      <div class="sheet">
        <h1>Capabilities</h1>
        <p class="lede">B &mdash; one choice per capability. The endings follow from it.</p>

        <div class="cap cap--open">
          <div class="cap__row">
            <span class="mono">CAP-01</span>
            <span class="cap__name">Identification</span>
            <span class="mark">Gating</span>
            <span class="grow"><button class="button button--primary">Save</button></span>
          </div>
          <div class="choose">
            <label class="side__label" for="e1" style="margin:0">Starts at</label>
            <select id="e1">
              <option>S-00 &mdash; The chat opens</option>
              <option>S-01 &mdash; Card number taken, checking it</option>
              <option>S-02 &mdash; Card not recognised, asked again</option>
            </select>
            <span class="arrow">only states that reach one of DEC-01, DEC-02 are listed</span>
          </div>
          <div class="flow">
            """ + _chip("S-00", "The chat opens", "in") + """
            <span class="arrow">&rarr; walks DEC-01, DEC-02 &rarr;</span>
            """ + _chip("S-03", "Identified from card details", "out") + """
            """ + _chip("S-04", "Identified by one-time code", "out") + """
            """ + _chip("S-05", "Not identified", "end") + """
          </div>
          <p class="derived"><b>3 endings, worked out from the graph.</b> Two hand on to
            Verification; one ends the interaction.
            <button class="link">Set them by hand instead</button></p>
        </div>

        <div class="cap">
          <div class="cap__row">
            <span class="mono">CAP-02</span>
            <span class="cap__name">Verification</span>
            <span class="mark">Gating</span>
            <span class="grow"><button class="button button--quiet">Change</button></span>
          </div>
          <div class="flow">
            """ + _chip("S-03", "Identified from card details", "in") + """
            """ + _chip("S-04", "Identified by one-time code", "in") + """
            <span class="arrow">&rarr;</span>
            """ + _chip("S-08", "Verified cardmember", "out") + """
            """ + _chip("S-09", "Passed to an adviser", "end") + """
          </div>
          <p class="derived">Entered two ways, so it is walked twice &mdash; once from each.
            <b>6 scenarios.</b></p>
        </div>

        <div class="cap">
          <div class="cap__row">
            <span class="mono">CAP-03</span>
            <span class="cap__name">Charge handling</span>
            <span class="mark">Transactional</span>
            <span class="grow"><button class="button button--quiet">Change</button></span>
          </div>
          <div class="flow">
            """ + _chip("S-08", "Verified cardmember", "in") + """
            <span class="arrow">&rarr;</span>
            """ + _chip("S-12", "Dispute filed", "end") + """
            """ + _chip("S-13", "Not disputable; reason given", "end") + """
          </div>
          <p class="warnrow"><b>Nothing hands on to this.</b> S-08 is an ending of Verification,
            so the chain is complete &mdash; but if it were not, this is where that would show.</p>
        </div>
      </div>
      <p class="legend"><b>How it reads.</b> A capability is one line until it needs attention:
      its span stated as chips, solid for the way in and dashed for the ways out, green where the
      interaction ends there. The only control in the ordinary case is one dropdown, and it is
      already pruned to the states that can reach a decision this capability owns.
      <b>What it costs:</b> a derivation function in <span class="mono">core/graph.py</span>, and
      the honesty to show its answer rather than hide it &mdash; the override is not optional,
      because a capability whose decisions are shared will derive badly.</p>""", CSS)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    Path(sys.argv[1]).write_text(build(), encoding="utf-8")
