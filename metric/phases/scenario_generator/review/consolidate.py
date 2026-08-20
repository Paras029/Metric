"""Folding a declaration's duplicates together, deterministically.

A drafted intake arrives with the same thing said more than once and said differently each time.
The model is filling six sheets that reference each other by id, from prose that references
nothing by id, and the failure is consistent: "Identity check" as a capability and "Identity
verification" as another; a tool called "Identity service" and a capability called "Identity
service"; the same persona under two ids because two documents described it in different words.

That was untidy when a capability was a label. It is expensive now that a capability is the block
the scenario space is enumerated over: two capabilities that are one capability produce two blocks
where there is one, each walked separately, each entered from wherever the split left it. A
duplicate persona multiplies the whole space.

Nothing here calls a model. Duplicates of this kind are a matching problem -- the same words in a
different order, a synonym, a plural -- and a deterministic pass that says exactly what it merged
is worth more than a call that might also decide two genuinely different things are the same. What
it cannot settle it leaves alone and says so, which is what the questions to the model owner are
for.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from metric.shared.text import name_key

logger = logging.getLogger(__name__)

_key = name_key


def _merge_rows(rows: List[dict], key_field: str, id_field: str = "id") -> Tuple[List[dict],
                                                                                Dict[str, str],
                                                                                List[str]]:
    """Fold rows whose names reduce to the same key. Returns (kept, renames, what was merged).

    The first row of each group wins its id, and every later one is remapped onto it, because the
    first is the one the rest of the declaration is most likely to already reference. Fields are
    filled in from the losers where the winner left them blank -- two half-filled duplicates
    should produce one filled row, not one half-filled row and a note about the other.
    """
    groups: Dict[str, List[dict]] = {}
    for row in rows:
        groups.setdefault(_key(row.get(key_field, "")) or str(row.get(id_field, "")),
                          []).append(row)

    kept: List[dict] = []
    renames: Dict[str, str] = {}
    merged: List[str] = []
    for group in groups.values():
        winner, losers = dict(group[0]), group[1:]
        for loser in losers:
            renames[str(loser.get(id_field, ""))] = str(winner.get(id_field, ""))
            for field, value in loser.items():
                if not str(winner.get(field, "")).strip() and str(value).strip():
                    winner[field] = value
            merged.append(f"{loser.get(id_field)} ({loser.get(key_field)}) into "
                          f"{winner.get(id_field)} ({winner.get(key_field)})")
        kept.append(winner)
    return kept, renames, merged


def consolidate(data: dict) -> Tuple[dict, List[str]]:
    """The declaration with its duplicates folded together. Returns (declaration, what changed).

    Order matters: capabilities are folded first, because decisions and tools point at them and
    both have to be repointed onto whichever id survived.
    """
    out = dict(data)
    notes: List[str] = []

    capabilities, renamed, merged = _merge_rows(list(out.get("capabilities") or []), "name")
    if merged:
        notes.append("Capabilities folded together: " + "; ".join(merged)
                     + ". Two capabilities that are one capability produce two blocks of the "
                       "scenario space where there is one.")
    out["capabilities"] = capabilities

    decisions = []
    for decision in (out.get("decisions") or []):
        row = dict(decision)
        row["capability_id"] = renamed.get(row.get("capability_id", ""), row.get("capability_id", ""))
        decisions.append(row)
    out["decisions"] = decisions

    tools = []
    for tool in (out.get("tools") or []):
        row = dict(tool)
        row["capability_id"] = renamed.get(row.get("capability_id", ""), row.get("capability_id", ""))
        tools.append(row)

    kept_tools, _, merged_tools = _merge_rows(tools, "name", id_field="name")
    if merged_tools:
        notes.append("Tools folded together: " + "; ".join(merged_tools) + ".")
    out["tools"] = kept_tools

    personas, _, merged_personas = _merge_rows(list(out.get("personas") or []), "name")
    if merged_personas:
        notes.append("Personas folded together: " + "; ".join(merged_personas)
                     + ". Every persona multiplies the whole scenario space, so a duplicate one "
                       "doubles a part of the pack without widening it.")
    out["personas"] = personas

    notes += _report_crossovers(out)
    for note in notes:
        logger.info("%s", note)
    return out, notes


def _report_crossovers(data: dict) -> List[str]:
    """Where a tool and a capability say the same thing, and where a link points at nothing.

    Reported rather than merged. A tool and a capability sharing a name is usually one modelled
    twice -- "Identity service" as the thing the agent can do and as the thing it calls -- but
    which of the two it should have been is a judgement about the agent, and guessing it would
    silently delete either a block of the scenario space or the record of a system being called.
    """
    notes: List[str] = []
    capabilities = {c.get("id", ""): c for c in (data.get("capabilities") or [])}
    by_name = {_key(c.get("name", "")): c for c in capabilities.values()}

    shared = [(t.get("name"), by_name[_key(t.get("name", ""))].get("id"))
              for t in (data.get("tools") or []) if _key(t.get("name", "")) in by_name]
    if shared:
        notes.append(
            "Named as both a tool and a capability: "
            + "; ".join(f"{name} and {cid}" for name, cid in shared)
            + ". One of the two is usually the same thing modelled twice — a capability is "
              "something the agent does, a tool is a system it calls to do it — but which one it "
              "should be is a judgement about the agent, so both are left as declared.")

    dangling = sorted({t.get("capability_id") for t in (data.get("tools") or [])
                       if t.get("capability_id") and t.get("capability_id") not in capabilities})
    if dangling:
        notes.append(f"Tools point at capabilities that do not exist: {', '.join(dangling)}. "
                     f"Those tools are attached to nothing and inform no scenario.")

    used = {d.get("capability_id") for d in (data.get("decisions") or []) if d.get("capability_id")}
    orphans = sorted(c for c in capabilities if c not in used)
    if orphans:
        notes.append(f"Capabilities no decision exercises: {', '.join(orphans)}. "
                     f"Nothing branches on them, so they contribute no scenarios.")
    return notes
