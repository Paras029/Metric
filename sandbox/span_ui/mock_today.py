"""What the panel looks like now, at a realistic size. The baseline the three proposals beat."""
from _shell import CAPABILITIES, STATES, page


def _list(name, chosen):
    rows = "".join(
        f'<li><label class="check"><input type="checkbox" name="{name}"'
        f'{" checked" if sid in chosen else ""}>'
        f'<span class="mono">{sid}</span> {text}'
        f'{" <span class=mark>ends</span>" if kind == "ends" else ""}</label></li>'
        for sid, text, kind, _ in STATES)
    return f'<ul class="span__list">{rows}</ul>'


def build() -> str:
    items = ""
    for cap in CAPABILITIES:
        items += f"""
        <li>
          <div class="cap__head">
            <span class="mono">{cap['id']}</span>
            <span class="cap__name">{cap['name']}</span>
            <span class="mark">{cap['type']}</span>
          </div>
          <p class="cap__note">Branched on at
            {', '.join(f'<span class="mono">{d}</span>' for d in cap['decisions'])}</p>
          <div class="span">
            <div class="span__side">
              <p class="span__label">Entered at</p>{_list('entry', cap['entry'])}
            </div>
            <div class="span__side">
              <p class="span__label">Hands on or ends at</p>{_list('exit', cap['exit'])}
            </div>
            <div class="span__save">
              <button class="button button--quiet">Save span</button>
              <span class="span__note">Walked as a block.</span>
            </div>
          </div>
        </li>"""

    return page("Capability spans as they are today", f"""
      <div class="sheet">
        <h1>Capabilities</h1>
        <p class="lede">Today &mdash; 3 capabilities, 14 states, 84 checkboxes.</p>
        <ul class="caps">{items}</ul>
      </div>
      <p class="legend"><b>The baseline.</b> Every state in the graph appears twice per
      capability. At fourteen states that is 84 checkboxes in six scrolling boxes; at the
      twenty-eight a real declaration carries it is 168, and the boxes look identical.</p>""")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    Path(sys.argv[1]).write_text(build(), encoding="utf-8")
