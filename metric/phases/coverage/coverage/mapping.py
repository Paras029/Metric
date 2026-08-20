"""Mapping the conversations the model owner actually ran onto the generated scenario space."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from metric.domain.models import ScenarioRow, IntakeData
from metric.phases.coverage.coverage.conversations import Conversation
from metric.shared import chunks
from metric.shared.replies import text as _text
from metric.llm import cancellation, config, council, prompts
from metric.llm.calling import call, parsed_reply
from metric.llm.describe import describe_use_case
from metric.llm.gateway import ask_llm

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
    """One conversation as text, trimmed from the middle rather than the end."""
    text = conversation.transcript
    if len(text) <= MAX_TRANSCRIPT_CHARS:
        return text
    head = MAX_TRANSCRIPT_CHARS // 3
    tail = MAX_TRANSCRIPT_CHARS - head
    return f"{text[:head]}\n[... {len(text) - MAX_TRANSCRIPT_CHARS} characters omitted ...]\n{text[-tail:]}"


def describe_scenario_space(scenarios: List[ScenarioRow], texts: Dict[str, str] = None) -> str:
    """The scenario space as the matcher sees it: one line per scenario, ending included."""
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
            intake: IntakeData, texts: Dict[str, str] = None,
            issued: Optional[List[ScenarioRow]] = None) -> List[Mapping]:
        """One mapping per conversation, in the order they were submitted."""
        cancellation.check(self._cancel)
        if not conversations:
            return []

        known = {s.id for s in scenarios}
        was_issued = known | {s.id for s in (issued or [])}

        # Anything the submission already labelled with one of our scenario ids is settled. That
        # only happens when the team filled in and returned the data template this tool issued, so
        # the id is not a claim to weigh -- it is the row they were asked to fill in, and asking a
        # model to rediscover it would be paying for an answer that is already written down. A
        # submission returned whole therefore costs no calls at all.
        stated = [c for c in conversations if c.scenario_id in was_issued]
        if stated:
            logger.info("%d of %d conversation(s) name the scenario they were run against; only "
                        "the other %d need a call.",
                        len(stated), len(conversations), len(conversations) - len(stated))
        settled = {c.id: Mapping(conversation_id=c.id, scenario_id=c.scenario_id,
                                 confidence="high", declared_group=c.group,
                                 reason="Stated on the returned data template.")
                   for c in stated}

        # An id the file names that this scenario space does not have is a real finding -- the
        # template was issued from an older space, or the row was hand-edited -- so it goes to the
        # call rather than being silently dropped or silently trusted.
        unlabelled = [c for c in conversations if c.id not in settled]
        if not unlabelled:
            return [settled[c.id] for c in conversations]

        space = describe_scenario_space(scenarios, texts)
        use_case = describe_use_case(intake)

        pending = list(chunks(unlabelled, self._batch))
        # Reported as replies land, not as they are applied: every chunk is in flight at once, so
        # the applying loop below runs in a fraction of a second after a wait of minutes.
        sizes = [len(chunk) for chunk in pending]
        replies = council.deliberate_batch(
            self._complete, prompts.load(_SYSTEM_PROMPT),
            [self._render(chunk, space, use_case) for chunk in pending],
            stage="COVERAGE_MAP",
            tier=config.stage_tier("COVERAGE_MAP", config.JUDGEMENT),
            max_concurrency=config.stage_concurrency("COVERAGE_MAP"), cancel=self._cancel,
            on_progress=lambda done, _total: self._progress(
                f"Mapped {len(settled) + sum(sizes[:done])} of {len(conversations)} conversations",
                len(settled) + sum(sizes[:done]), len(conversations)))

        mapped: List[Mapping] = dict(settled)
        done = len(settled)
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
                mapped[conversation.id] = mapping
            done += len(chunk)
            self._progress(f"Mapped {done} of {len(conversations)} conversations",
                           done, len(conversations))

        # Back into submission order. The mappings are written beside the conversations in the
        # coverage report, so a list that came back grouped by how it was worked out would read
        # as a reordering of the model owner's file.
        ordered = [mapped[c.id] for c in conversations if c.id in mapped]
        unmatched = sum(1 for m in ordered if not m.matched)
        logger.info("Mapped %d conversation(s) onto %d scenario(s); %d matched nothing.",
                    len(ordered), len({m.scenario_id for m in ordered if m.matched}), unmatched)
        return ordered

    def _render(self, chunk: List[Conversation], space: str, use_case: str) -> str:
        """The prompt for one chunk. The declared label is shown but marked as not evidence."""
        payload = [{
            "id": conversation.id,
            "filed_by_the_model_owner_as": conversation.group or "(not grouped)",
            "transcript": _transcript(conversation),
        } for conversation in chunk]
        return prompts.render(
            _MAP_PROMPT, use_case=use_case, scenarios=space,
            conversations=json.dumps(payload, indent=2))

    def _apply(self, chunk: List[Conversation], reply, known: set) -> List[Mapping]:
        """Read one chunk's reply, discarding a scenario id the scenario space does not have."""
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
        """One conversation a batch dropped, asked about on its own."""
        chunk = [conversation]
        try:
            reply = call(self._complete, prompts.load(_SYSTEM_PROMPT),
                         self._render(chunk, space, use_case),
                         tier=config.stage_tier("COVERAGE_MAP", config.JUDGEMENT))
        except Exception as exc:                           # handled the same way _apply handles
            reply = exc                                     # a failed call inside a batch
        return self._apply(chunk, reply, known)[0]
