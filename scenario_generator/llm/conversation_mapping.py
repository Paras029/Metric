"""Mapping the conversations the model owner actually ran onto the generated scenario space.

The coverage question is "what does the model owner's testing already cover", and the honest way
to answer it is from the transcripts rather than from whatever the transcripts were filed under.
The model owner's own scenario labels are frequently absent, and where present are frequently the
thing being checked.

One conversation maps to at most one scenario, and the discriminator is **where it ends**. A
scenario is a complete route to a specific ending, and some routes are prefixes of
others: a conversation that authenticates and stops is not the same test as one that authenticates
and then goes on to verify a charge, even though the second contains the first. Matching on the
route alone silently files the short one under the long one and reports coverage that does not
exist. The prompt is explicit about this because it is the mistake that matters.

"Nothing fits" is a first-class answer, not a failure. It means either the model owner is testing
something the scenario space never enumerated -- worth knowing, and a candidate to add -- or that
conversation is not a test of this agent. Either reading is more useful than a nearest-fit match
nobody can trust.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..core.models import ScenarioRow, IntakeData
from ..ingest.conversations import Conversation
from ..utils import chunks
from ..utils.replies import text as _text
from . import cancellation, config, prompt_loader
from .calling import call, call_batch, parsed_reply
from .context import describe_use_case
from .gateway import ask_llm

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "coverage.system"
_MAP_PROMPT = "coverage.map"

CONFIDENCE = ("high", "medium", "low")

# How many conversations go into one call. Smaller than the other batched passes on purpose: a
# transcript is many times the size of a scenario description, and every call has to carry the
# whole scenario space alongside them for the match to be possible at all.
DEFAULT_BATCH_SIZE = 5

# How much of one transcript is sent. Long enough to carry the arc and the ending, which is what
# the match turns on; short enough that a handful of them plus the scenario space fits comfortably.
MAX_TRANSCRIPT_CHARS = 4000


@dataclass
class Mapping:
    """One conversation, and the scenario space scenario it demonstrates."""

    conversation_id: str
    scenario_id: str = ""
    confidence: str = "low"
    intent: str = ""
    ending: str = ""
    reason: str = ""
    declared_group: str = ""
    """What the model owner filed this conversation under, where it was filed at all.

    Carried through the mapping untouched and never shown to the call that decides the match --
    see :func:`_render`. It exists so the model owner's grouping can be compared with the
    validator's afterwards.
    """

    answered: bool = True
    """Whether the call actually returned a verdict for this conversation.

    "No scenario fits" and "the call never came back about it" both leave ``scenario_id`` empty
    but mean opposite things -- the first is a finding, the second is a dropped chunk. Kept apart
    so the second can be retried rather than reported as a gap in the model owner's testing.
    """

    @property
    def matched(self) -> bool:
        return bool(self.scenario_id)


def _transcript(conversation: Conversation) -> str:
    """One conversation as text, trimmed from the middle rather than the end.

    The ending is what the match turns on, so it is the one part that must survive truncation. A
    transcript cut at the front loses how the user opened, which the intent can usually be
    recovered from; cut at the back it loses the answer.
    """
    text = conversation.transcript
    if len(text) <= MAX_TRANSCRIPT_CHARS:
        return text
    head = MAX_TRANSCRIPT_CHARS // 3
    tail = MAX_TRANSCRIPT_CHARS - head
    return f"{text[:head]}\n[... {len(text) - MAX_TRANSCRIPT_CHARS} characters omitted ...]\n{text[-tail:]}"


def describe_scenario_space(scenarios: List[ScenarioRow], texts: Dict[str, str] = None) -> str:
    """The scenario space as the matcher sees it: one line per scenario, ending included.

    The ending is spelled out rather than left implicit in the route, because it is the field the
    match is decided on and a decision path alone does not say where it stops.
    """
    texts = texts or {}
    lines = []
    for scenario in scenarios:
        description = texts.get(scenario.id, "")
        detail = f" — {description[:200]}" if description else ""
        lines.append(f"- {scenario.id} [{scenario.category}] route: {scenario.path_str or 'probe'}"
                     f"{detail}")
    return "\n".join(lines) or "- none"


class ConversationMapper:
    """Maps submitted conversations onto the scenario space, one scenario each at most."""

    def __init__(self, complete: Optional[Callable[..., str]] = None, batch_size: int = None,
                 progress: Optional[Callable[..., None]] = None, cancel=None) -> None:
        self._complete = complete or ask_llm
        self._batch = (batch_size if batch_size is not None
                       else config.stage_batch_size("COVERAGE_MAP", DEFAULT_BATCH_SIZE))
        self._progress = progress or (lambda *a, **k: None)
        self._cancel = cancel

    def map(self, conversations: List[Conversation], scenarios: List[ScenarioRow],
            intake: IntakeData, texts: Dict[str, str] = None) -> List[Mapping]:
        """One mapping per conversation, in the order they were submitted."""
        cancellation.check(self._cancel)
        if not conversations:
            return []

        known = {s.id for s in scenarios}
        space = describe_scenario_space(scenarios, texts)
        use_case = describe_use_case(intake)

        pending = list(chunks(conversations, self._batch))
        # Reported as replies land, not as they are applied: every chunk is in flight at once, so
        # the applying loop below runs in a fraction of a second after a wait of minutes.
        sizes = [len(chunk) for chunk in pending]
        replies = call_batch(
            self._complete, prompt_loader.load(_SYSTEM_PROMPT),
            [self._render(chunk, space, use_case) for chunk in pending],
            tier=config.stage_tier("COVERAGE_MAP", config.JUDGEMENT),
            max_concurrency=config.stage_concurrency("COVERAGE_MAP"), cancel=self._cancel,
            on_progress=lambda done, _total: self._progress(
                f"Mapped {sum(sizes[:done])} of {len(conversations)} conversations",
                sum(sizes[:done]), len(conversations)))

        mapped: List[Mapping] = []
        done = 0
        for chunk, reply in zip(pending, replies):
            cancellation.check(self._cancel)
            for mapping, conversation in zip(self._apply(chunk, reply, known), chunk):
                # A conversation a batch dropped is asked about on its own. It matters more here
                # than in the other batched passes: a dropped conversation would otherwise be
                # reported as matching nothing, which reads as a gap in the model owner's testing
                # rather than as a call that did not come back, and understating coverage is
                # the one error this stage must not make quietly.
                if not mapping.answered:
                    cancellation.check(self._cancel)
                    mapping = self._map_one(conversation, space, use_case, known)
                mapped.append(mapping)
            done += len(chunk)
            self._progress(f"Mapped {done} of {len(conversations)} conversations",
                           done, len(conversations))

        unmatched = sum(1 for m in mapped if not m.matched)
        logger.info("Mapped %d conversation(s) onto %d scenario(s); %d matched nothing.",
                    len(mapped), len({m.scenario_id for m in mapped if m.matched}), unmatched)
        return mapped

    def _render(self, chunk: List[Conversation], space: str, use_case: str) -> str:
        """The prompt for one chunk. The declared label is shown but marked as not evidence."""
        payload = [{
            "id": conversation.id,
            "filed_by_the_model_owner_as": conversation.group or "(not grouped)",
            "transcript": _transcript(conversation),
        } for conversation in chunk]
        return prompt_loader.render(
            _MAP_PROMPT, use_case=use_case, scenarios=space,
            conversations=json.dumps(payload, indent=2))

    def _apply(self, chunk: List[Conversation], reply, known: set) -> List[Mapping]:
        """Read one chunk's reply, discarding a scenario id the scenario space does not have.

        An unrecognised id is treated as no match rather than kept: coverage claimed against a
        scenario that does not exist is worse than coverage not claimed, because it inflates the
        figure the whole stage exists to report.
        """
        parsed = parsed_reply(reply, "Coverage mapping call",
                              ", ".join(c.id for c in chunk))
        mappings = []
        for conversation in chunk:
            entry = parsed.get(conversation.id)
            mapping = Mapping(conversation_id=conversation.id,
                              declared_group=conversation.group)
            if isinstance(entry, dict):
                scenario_id = _text(entry, "scenario_id")
                if scenario_id and scenario_id not in known:
                    logger.warning("Mapping for %s named %s, which is not in the scenario space; "
                                   "treated as no match.", conversation.id, scenario_id)
                    scenario_id = ""
                confidence = _text(entry, "confidence").lower()
                mapping.scenario_id = scenario_id
                mapping.confidence = confidence if confidence in CONFIDENCE else "low"
                mapping.intent = _text(entry, "intent")
                mapping.ending = _text(entry, "ending")
                mapping.reason = _text(entry, "reason")
            else:
                mapping.answered = False
                mapping.reason = "The mapping call returned nothing for this conversation."
            mappings.append(mapping)
        return mappings

    def _map_one(self, conversation: Conversation, space: str, use_case: str,
                 known: set) -> Mapping:
        """One conversation a batch dropped, asked about on its own.

        Rare enough, and small enough, that batching the mop-up too would not be worth the
        complexity -- most runs refill nothing at all.
        """
        chunk = [conversation]
        try:
            reply = call(self._complete, prompt_loader.load(_SYSTEM_PROMPT),
                         self._render(chunk, space, use_case),
                         tier=config.stage_tier("COVERAGE_MAP", config.JUDGEMENT))
        except Exception as exc:                           # handled the same way _apply handles
            reply = exc                                     # a failed call inside a batch
        return self._apply(chunk, reply, known)[0]
