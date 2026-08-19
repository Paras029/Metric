"""Shared page furniture for the three mockups, so the comparison is about the panel only."""
from pathlib import Path

CSS = (Path(__file__).resolve().parents[2]
       / "scenario_generator/webapp/static/app.css").read_text(encoding="utf-8")

EXTRA = """
body { background: var(--canvas); color: var(--ink); font-family: var(--font-sans);
       font-size: var(--t-ui); margin: 0; padding: var(--s6); }
.sheet { max-width: 62rem; margin: 0 auto; background: var(--surface);
         border: 1px solid var(--rule); border-radius: var(--r-md); padding: var(--s5); }
.sheet > h1 { font-size: var(--t-title); margin: 0 0 var(--s2); letter-spacing: -.01em; }
.sheet > .lede { color: var(--ink-dim); font-size: var(--t-meta); margin: 0 0 var(--s5); }
.caps { list-style: none; margin: 0; padding: 0; }
.caps > li { border-top: 1px solid var(--rule); padding: var(--s4) 0; }
.caps > li:first-child { border-top: 2px solid var(--ink); }
.cap__head { display: flex; align-items: baseline; gap: var(--s3); flex-wrap: wrap; }
.cap__name { font-weight: 650; }
.cap__note { color: var(--ink-dim); font-size: var(--t-meta); margin: var(--s1) 0 0; }
.button { font: inherit; font-size: var(--t-small); padding: .3rem .7rem; cursor: pointer;
          border: 1px solid var(--rule-strong); background: var(--surface); color: var(--ink);
          border-radius: var(--r-sm); }
.button--quiet { border-color: var(--rule); color: var(--ink-mid); }
.button--primary { background: var(--blue); border-color: var(--blue-dark); color: #fff; }
.legend { margin: var(--s6) auto 0; max-width: 62rem; font-size: var(--t-meta);
          color: var(--ink-dim); border-top: 1px solid var(--rule); padding-top: var(--s3); }
.legend b { color: var(--ink); font-weight: 650; }
"""


def page(title: str, body: str, css: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>{CSS}</style><style>{EXTRA}</style><style>{css}</style></head>
<body>{body}</body></html>"""


# One realistic declaration, used by all three. Three capabilities over fourteen states, which is
# already past the size the present control is comfortable at and well short of a real one.
CAPABILITIES = [
    {"id": "CAP-01", "name": "Identification", "type": "Gating",
     "decisions": ["DEC-01", "DEC-02"], "entry": ["S-00"], "exit": ["S-03", "S-04", "S-05"]},
    {"id": "CAP-02", "name": "Verification", "type": "Gating",
     "decisions": ["DEC-03", "DEC-04", "DEC-05"], "entry": ["S-03", "S-04"],
     "exit": ["S-08", "S-09"]},
    {"id": "CAP-03", "name": "Charge handling", "type": "Transactional",
     "decisions": ["DEC-06", "DEC-07"], "entry": ["S-08"], "exit": ["S-12", "S-13"]},
]

STATES = [
    ("S-00", "The chat opens", "start", None),
    ("S-01", "Card number taken, checking it", "", "CAP-01"),
    ("S-02", "Card not recognised, asked again", "", "CAP-01"),
    ("S-03", "Identified from card details", "", "CAP-01"),
    ("S-04", "Identified by one-time code", "", "CAP-01"),
    ("S-05", "Not identified; chat ended", "ends", "CAP-01"),
    ("S-06", "Security question asked", "", "CAP-02"),
    ("S-07", "Answer wrong, one try left", "", "CAP-02"),
    ("S-08", "Verified cardmember on a clear account", "", "CAP-02"),
    ("S-09", "Verification failed; passed to an adviser", "ends", "CAP-02"),
    ("S-10", "Charge identified, asking for a reason", "", "CAP-03"),
    ("S-11", "Reason given, checking eligibility", "", "CAP-03"),
    ("S-12", "Dispute filed", "ends", "CAP-03"),
    ("S-13", "Not disputable; reason given", "ends", "CAP-03"),
]
