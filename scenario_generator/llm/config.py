"""Model configuration. Everything sensitive comes from the environment (.env).

Calls go through SafeChain, which owns authentication, token refresh and the request shape each
model expects. Nothing here mints a token or builds a payload: SafeChain reads its credentials
from the environment and the per-model payload structures from the YAML at ``CONFIG_PATH``, and
hands back a LangChain chat model. What is left for this file is which model each kind of work
should use and how much room to give it.

Calls are grouped into three tiers, and each tier picks its own model, output cap and reasoning
effort. The tiers exist because the work is genuinely different, not to save money for its own
sake: a pass that maps free text onto a fixed vocabulary is doing classification, and giving it
the same model and reasoning budget as a pass that must read sixty pages and justify a verdict
makes it slower without making it better.

Every tier defaults to the main model, so nothing changes until a smaller one is configured. That
matters where a new model has to clear an approval before it can be used.
"""
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- SafeChain
#
# Three variables and a YAML file, all read by SafeChain itself rather than by anything here.
CONSUMER_SECRET = "CIBIS_CONSUMER_SECRET"
CONSUMER_INTEGRATION_ID = "CIBIS_CONSUMER_INTEGRATION_ID"
CONFIG_PATH = "CONFIG_PATH"

DEFAULT_CONFIG_PATH = "config.yml"


def _pad_base64(secret: str) -> str:
    """Restore the padding a base64 secret needs to decode.

    The portal issues the consumer secret with its trailing ``=`` stripped, and the decoder will
    not accept it in that state. Padding it back out to a multiple of four is the whole fix, and
    doing it here means nobody has to remember to paste the padding in by hand -- a mistake that
    surfaces as an authentication failure with nothing to suggest the cause.
    """
    secret = (secret or "").strip()
    return secret + "=" * (-len(secret) % 4) if secret else ""


def prepare_environment() -> None:
    """Put the credentials where SafeChain expects to find them.

    Called before SafeChain is imported. It reads these straight out of the process environment,
    so the padding fix has to land there rather than being passed as an argument.
    """
    secret = _pad_base64(os.getenv(CONSUMER_SECRET, ""))
    if secret:
        os.environ[CONSUMER_SECRET] = secret
    os.environ.setdefault(CONFIG_PATH, DEFAULT_CONFIG_PATH)


def missing_credentials() -> list:
    """Which required variables are unset, so a run can say so before it starts calling."""
    return [name for name in (CONSUMER_SECRET, CONSUMER_INTEGRATION_ID)
            if not os.getenv(name, "").strip()]


# The model each tier asks SafeChain for. These are the names SafeChain knows, which are the keys
# in the config.yml the team shares -- not a provider's own path-style identifier.
LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "gemini-2.5-pro")

# Whether the configured model accepts images alongside text. Set LLM_VISION=off where the
# gateway rejects the multimodal request shape -- diagram reading then degrades to asking a
# person to describe the flow, rather than the run failing.
LLM_VISION = os.getenv("LLM_VISION", "on").strip().lower() not in ("0", "off", "false", "no")

# Base64 inflates an image by about a third, and gateways cap the request body. Anything larger
# is refused with a reason rather than sent and rejected.
MAX_IMAGE_BYTES = int(os.getenv("LLM_MAX_IMAGE_BYTES", "4000000"))

DEFAULT_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))


# --------------------------------------------------------------------------- redaction
#
# Submitted documents routinely carry names, account numbers and other business-sensitive
# material that has no reason to reach a model call. Every segment ingestion reads a document
# into is redacted before it is joined into a corpus -- see ingest/redaction.py, which is the
# only place these are read. Off by default so the tool runs unmodified wherever the internal
# redaction package (pii-redactor, built on ee_utils.redaction) has not been installed; turn it
# on once it has, for anything but a local run against material that was never sensitive.
PII_REDACTION = os.getenv("PII_REDACTION", "off").strip().lower() not in ("0", "off", "false", "no")

# masking | substitution | disabled, and the text a masked span is replaced with.
PII_REDACTION_MODE = os.getenv("PII_REDACTION_MODE", "masking")
PII_REDACTION_REPLACEMENT_TEXT = os.getenv("PII_REDACTION_REPLACEMENT_TEXT", "[REDACTED]")

# strict | balanced | loose -- how aggressive the engine's fallback masking is. Left unset
# (rather than defaulted here) so the engine's own default applies unless a value is given.
PII_REDACTION_SENSITIVITY = os.getenv("PII_REDACTION_SENSITIVITY", "").strip() or None


def _csv(raw: str) -> list:
    return [item.strip() for item in raw.split(",") if item.strip()]


# Detector labels to switch off, and terms to leave alone regardless of what the engine would
# otherwise mask -- both comma-separated, e.g. PII_REDACTION_ALLOW=American Express,New York.
PII_REDACTION_EXCLUDE_ENTITIES = _csv(os.getenv("PII_REDACTION_EXCLUDE_ENTITIES", ""))
PII_REDACTION_ALLOW = _csv(os.getenv("PII_REDACTION_ALLOW", ""))


def _thresholds(raw: str) -> dict:
    """``name=score,name=score`` from the environment, as the ``{"name": {"score": score}}``
    shape the engine expects -- e.g. PII_REDACTION_THRESHOLDS=secondary_pii_email=0.7."""
    thresholds = {}
    for item in _csv(raw):
        name, _, score = item.partition("=")
        name, score = name.strip(), score.strip()
        if not name or not score:
            continue
        try:
            thresholds[name] = {"score": float(score)}
        except ValueError:
            logger.warning("PII_REDACTION_THRESHOLDS: '%s' is not name=score; ignored.", item)
    return thresholds


PII_REDACTION_THRESHOLDS = _thresholds(os.getenv("PII_REDACTION_THRESHOLDS", ""))


# How many times a call is retried before it is reported as failed, where a tier does not set its
# own. Retries cover a gateway that is busy or briefly unreachable; anything the model rejects on
# its merits is raised, since repeating a malformed request only wastes the time it takes to fail
# again. A tier under heavier concurrent load -- more chunks in flight, each one retrying -- can
# turn a brief gateway hiccup into a pile of simultaneous retries, which is what the per-tier
# override below exists to relieve independently of this default.
DEFAULT_MAX_ATTEMPTS = int(os.getenv("LLM_MAX_ATTEMPTS", "4"))


@dataclass(frozen=True)
class Tier:
    """A model, an output cap, a reasoning effort, a temperature and a retry count, chosen
    together for one kind of work."""

    name: str
    model: str
    max_tokens: int
    reasoning_effort: str
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    temperature: float = None                             # set below; None means "use the default"


def _tier(name: str, prefix: str, max_tokens: int, effort: str,
         max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> Tier:
    """Build a tier from the environment, falling back to the main model where none is set."""
    return Tier(
        name=name,
        model=os.getenv(f"{prefix}_MODEL_ID", "").strip() or LLM_MODEL_ID,
        max_tokens=int(os.getenv(f"{prefix}_MAX_TOKENS", str(max_tokens))),
        reasoning_effort=os.getenv(f"{prefix}_REASONING_EFFORT", effort),
        max_attempts=int(os.getenv(f"{prefix}_MAX_ATTEMPTS", str(max_attempts))),
        temperature=float(os.getenv(f"{prefix}_TEMPERATURE", str(DEFAULT_TEMPERATURE))),
    )


# Reading documents, drafting the intake, weighing materiality, reviewing the benchmark. These
# read a lot, reason at length, and must justify what they conclude.
#
# The output cap is the model's own ceiling rather than half of it. Reasoning tokens are drawn
# from the same budget as the reply, so a high effort against a small cap truncates the JSON
# instead of shortening the answer -- which is why the cap and the effort are set together and
# why the cap is generous.
JUDGEMENT = _tier("judgement", "LLM_JUDGEMENT", 65_536, "high")

# Weighing materiality. Judgement-shaped work -- it reasons about a scenario against its peers --
# but unlike drafting or review it runs as many concurrent chunk calls as the benchmark has
# chunks, all against the same tier at once. That concurrency is what turns an ordinary gateway
# hiccup into a pile of simultaneous retries, so this tier gets its own retry ladder, shorter than
# the default, and its own model setting, separate from JUDGEMENT, so it can be pointed at
# something smaller without changing what drafting or review use.
MATERIALITY = _tier("materiality", "LLM_MATERIALITY", 32_000, "medium", max_attempts=2)

# Writing each scenario up for the modelling team. Mechanical and bounded by the batch size, but
# the text is issued and read by people, so it stays on the main model by default.
STANDARD = _tier("standard", "LLM", 16_000, "minimal")

# Mapping the modelling team's free-text scenarios onto the intake's declared vocabulary. This is
# classification against a closed list, with validation afterwards discarding anything outside
# it -- the cheapest useful model that can follow the format is enough. Set LLM_FAST_MODEL_ID to
# point it somewhere smaller.
FAST = _tier("fast", "LLM_FAST", 8_000, "minimal")


# --------------------------------------------------------------------------- per-stage overrides
#
# A tier is shared by every call doing the same *kind* of work, and that is coarse on purpose --
# pointing MATERIALITY at a smaller model changes every materiality-shaped judgement at once,
# which is usually what is wanted. Sometimes it is not: weighing a scenario's materiality and
# checking the review's declared category are both MATERIALITY-tier work, but they are two
# different calls in two different passes, run at two different points in the pipeline, and a
# benchmark can want them tuned differently -- a smaller model for the mechanical category check,
# the full one for materiality itself.
#
# STAGE_KEYS names every individual call site this way, one level finer than the tier. Each is
# overridden with ``LLM_STAGE_<key>_MODEL_ID`` / ``_MAX_TOKENS`` / ``_TEMPERATURE`` /
# ``_REASONING_EFFORT`` / ``_MAX_ATTEMPTS`` / ``_BATCH_SIZE`` (batch size only where the call
# actually batches), and every field defaults to its tier's own value where the stage does not set
# one -- so setting nothing here changes nothing, and setting one field of one stage leaves the
# rest of that stage, and every other stage, exactly on its tier's setting. That in turn falls
# back to the master model (``LLM_MODEL_ID``) wherever the tier itself does not override it, which
# is the three-level cascade this whole module builds: stage, then tier, then master.
STAGE_KEYS = (
    "INGEST_READ", "INGEST_RESOLVE", "INGEST_DIAGRAM_READ", "INGEST_DIAGRAM_SYNTHESIZE",
    "INGEST_DIAGRAM_REPAIR", "INTAKE_DRAFT", "STRUCTURE_REVIEW", "WRITER", "MATERIALITY_ASSESS",
    "REVIEWER_ASSESS", "REVIEWER_CATEGORY", "REVIEWER_PROPOSE", "OWNER_EXTRACT",
)


def stage_tier(stage: str, base: Tier) -> Tier:
    """``base`` with any ``LLM_STAGE_<stage>_*`` override applied, field by field.

    Read at the point of use rather than built once, the same reason :func:`max_corpus_chars` is a
    function and not a constant: the interface runs for hours, and a value fixed at import cannot
    be changed without restarting it.
    """
    prefix = f"LLM_STAGE_{stage.upper()}"
    return Tier(
        name=f"{base.name}:{stage.lower()}",
        model=os.getenv(f"{prefix}_MODEL_ID", "").strip() or base.model,
        max_tokens=int(os.getenv(f"{prefix}_MAX_TOKENS", str(base.max_tokens))),
        reasoning_effort=os.getenv(f"{prefix}_REASONING_EFFORT", base.reasoning_effort),
        max_attempts=int(os.getenv(f"{prefix}_MAX_ATTEMPTS", str(base.max_attempts))),
        temperature=float(os.getenv(f"{prefix}_TEMPERATURE", str(base.temperature))),
    )


def stage_batch_size(stage: str, default: int) -> int:
    """How many items one call in ``stage`` covers, or ``default`` where nothing overrides it."""
    return int(os.getenv(f"LLM_STAGE_{stage.upper()}_BATCH_SIZE", str(default)))


# How much submitted text goes into one reading call. Gemini 2.5 Pro takes about a million tokens
# of input; four characters to a token puts this near half a million tokens, which leaves ample
# room for the prompt and the reply. A pack larger than this is split across calls, which reads
# worse, so the number is set to make that rare.
MAX_CORPUS_CHARS = int(os.getenv("LLM_MAX_CORPUS_CHARS", "2000000"))


def max_corpus_chars() -> int:
    """The corpus limit, read at the point of use.

    A function rather than the constant above because the interface runs for hours and a value
    fixed at import cannot be changed without a restart.
    """
    return int(os.getenv("LLM_MAX_CORPUS_CHARS", str(MAX_CORPUS_CHARS)))


# How many times a question the first reading left open is put back to the documents before it is
# put to the modelling team. Each pass is one call over the whole corpus, and each one that lands
# saves the team a question. The last pass also triages what is left: a question only a person can
# answer is worth asking, and one that does not change what gets tested is not worth anyone's time.
INGEST_RESOLVE_PASSES = int(os.getenv("LLM_INGEST_RESOLVE_PASSES", "2"))

# Batching is two separate numbers, and it is easy to conflate them. "Batch size" (stage_batch_size,
# above) is how many rows -- scenarios, chunks of text -- go into the payload of *one* call: it
# decides call *count* for a fixed amount of work, and a bigger batch is fewer, larger calls, not
# faster ones. MAX_CONCURRENCY is how many of those calls are ever in flight to the gateway *at
# once*: it decides call *overlap*, not count, and does not change what any single call is asked to
# judge. Turning batch size down and concurrency up sends more, smaller calls, more of them at
# once, which is usually faster and always cheaper per call to retry; turning batch size up sends
# fewer, larger calls that each risk more work if one of them fails.
MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "4"))


def stage_concurrency(stage: str, default: int = None) -> int:
    """How many of one stage's batched calls run at once, from ``LLM_STAGE_<stage>_CONCURRENCY``.

    Falls back to ``default`` where the caller has one worth preferring over the global cap (a
    pass with unusually large individual calls, say), and from there to ``LLM_MAX_CONCURRENCY`` --
    the same stage-then-global shape as :func:`stage_tier`, minus the tier step, since concurrency
    is not a property of a tier.
    """
    fallback = default if default is not None else MAX_CONCURRENCY
    return int(os.getenv(f"LLM_STAGE_{stage.upper()}_CONCURRENCY", str(fallback)))

# Kept for callers that still read the older names.
DEFAULT_MAX_TOKENS = STANDARD.max_tokens
JUDGEMENT_MAX_TOKENS = JUDGEMENT.max_tokens
REASONING_EFFORT = STANDARD.reasoning_effort
JUDGEMENT_REASONING_EFFORT = JUDGEMENT.reasoning_effort
