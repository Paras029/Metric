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
from pathlib import Path

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


# --------------------------------------------------------------------------- where settings live
#
# Two files, split by who owns the answer.
#
# ``.env`` holds what is yours and your machine's: credentials, which model to call, where
# SafeChain is. It is never committed, differs per person, and is short enough to read at a glance.
#
# ``tuning.yml`` holds how the work is run: output caps, reasoning effort, batch sizes,
# concurrency, how hard ingestion tries. None of it is secret, all of it is worth a team agreeing
# on once, and as a table it is legible in a way forty ``KEY=value`` lines with comments between
# them are not. It is committed, so a change to it is reviewable like any other change.
#
# An environment variable still wins over the file wherever both are set. That is what keeps an
# existing ``.env`` working untouched, and leaves a way to override one setting on one machine for
# one run without editing a shared file.
DEFAULT_TUNING_PATH = "tuning.yml"

# Cached against the file's own modification time and size, not merely its path. A cache keyed on
# the path alone is read once and then answers from memory for the life of the process, which for
# an interface that runs for hours means an edit to the file changes nothing at all and gives no
# sign of it -- the single most confusing way a settings file can fail. Re-stating the file per
# lookup costs a syscall against a network round trip, which is not a trade worth thinking about.
_tuning_cache: dict = {}

# Said once per file, so a run states which settings are actually in force. A tuning file that was
# never found is otherwise indistinguishable from one whose values happen to match the defaults.
_announced: set = set()


def tuning_path() -> str:
    """Where the tuning file is, searched rather than assumed.

    ``TUNING_PATH`` names it outright. Otherwise it is looked for in the working directory and
    then upwards from it, and finally beside the installed package -- because the file is found
    relative to *something*, and a bare relative path silently finds nothing whenever the tool is
    launched from anywhere but the directory it happens to sit in. Silently: every setting in it
    reverts to its built-in default and nothing says so.
    """
    named = os.getenv("TUNING_PATH", "").strip()
    if named:
        return named

    here = Path.cwd()
    for directory in (here, *here.parents):
        candidate = directory / DEFAULT_TUNING_PATH
        if candidate.is_file():
            return str(candidate)

    # Where it lives in a source checkout, for a tool launched from somewhere else entirely.
    packaged = Path(__file__).resolve().parent.parent.parent / DEFAULT_TUNING_PATH
    if packaged.is_file():
        return str(packaged)
    return DEFAULT_TUNING_PATH


def _signature(path: Path):
    """What has to change for the file to be worth reading again."""
    try:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


class BrokenTuningFile(RuntimeError):
    """The tuning file exists and is not valid YAML."""


def _parse(path: Path) -> dict:
    """The file's contents, or raise :class:`BrokenTuningFile` naming what is wrong with it."""
    import yaml

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BrokenTuningFile(f"{path} is not valid YAML.\n\n{exc}") from exc
    if loaded is not None and not isinstance(loaded, dict):
        raise BrokenTuningFile(f"{path} is a {type(loaded).__name__}, not a mapping of settings.")
    return loaded or {}


def tuning() -> dict:
    """The tuning file as it is on disk right now. An absent file means built-in defaults.

    A file that is *present and broken* is the case worth being careful about. Falling back to the
    defaults there is the worst available behaviour: every value the file was setting silently
    reverts, the run proceeds and produces plausible output, and the only sign is one warning in a
    log nobody is reading. A batch size and a model choice both go quietly back to what they were.

    So a broken file never degrades to defaults. On the first read -- startup -- it raises, and
    :func:`check_tuning` turns that into a message and a refusal to start, which is cheap and
    unmissable. On a later read, where an edit has broken a file that was working, the last good
    values are kept and the problem is logged as an error: a run already under way should not
    change what it is doing halfway through because somebody mistyped a line in another window.
    """
    name = tuning_path()
    path = Path(name)
    signature = _signature(path)

    cached = _tuning_cache.get(name)
    if cached is not None and cached[0] == signature:
        return cached[1]

    if signature is None:                                  # no file at all: defaults, said once
        _tuning_cache[name] = (signature, {})
        if name not in _announced:
            _announced.add(name)
            logger.warning(
                "No tuning file at %s, so every setting is at its built-in default. Set "
                "TUNING_PATH if yours is elsewhere.", path)
        return {}

    try:
        values = _parse(path)
    except BrokenTuningFile as exc:
        if cached is None:
            raise
        # Keep what was working, and keep saying so: this is not a warning to be missed.
        logger.error("%s\n\nThe settings from before the edit are still in force. Nothing has "
                     "reverted to a default.", exc)
        _tuning_cache[name] = (signature, cached[1])
        return cached[1]

    _tuning_cache[name] = (signature, values)
    if name not in _announced:
        _announced.add(name)
        logger.info("Settings from %s.", path)
    elif cached is not None:
        logger.info("%s changed; the new settings apply from the next call.", path)
    return values


def check_tuning() -> None:
    """Read the tuning file once at startup so a broken one stops the run before it starts.

    Called by both front ends before anything else happens. A settings file that cannot be parsed
    is a mistake somebody made seconds ago and can fix in seconds; the expensive version is the one
    where the run goes ahead on defaults and the mistake is found in the output an hour later.
    """
    tuning()


def reload_tuning() -> None:
    """Forget the cached tuning file.

    Rarely needed: :func:`tuning` already re-reads the file whenever it has changed on disk, so an
    edit takes effect on its own. This exists for a test that writes a file within the same clock
    tick as the last read, and for forcing the announcement again.
    """
    _tuning_cache.clear()
    _announced.clear()


def _from_file(*path: str):
    """One value from the tuning file by its path, or None where it is not set."""
    node: object = tuning()
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _truthy(value: object) -> bool:
    return str(value).strip().lower() not in ("0", "off", "false", "no", "")


def setting(env: str, *path: str, default=None, cast=None):
    """One setting: the environment first, then the tuning file, then the built-in default.

    ``cast`` is applied to whichever of the first two supplied it, so a value typed as an int in
    the YAML and the same value typed as a string in the environment arrive here the same way.
    """
    raw = os.getenv(env)
    if raw is None or not str(raw).strip():
        raw = _from_file(*path)
    if raw is None:
        return default
    if cast is None:
        return raw
    try:
        return cast(raw)
    except (TypeError, ValueError):
        logger.warning("%s is not a valid value (%r); using %r.", env, raw, default)
        return default


# ----------------------------------------------------------------- resolved when they are read
#
# Everything from here down is a function, and the module answers the familiar constant names --
# ``config.JUDGEMENT``, ``config.PII_REDACTION`` -- from those functions, via :func:`__getattr__`
# at the foot of the file. A call site reads them as plain module attributes; each one is resolved
# at the moment it is read.
#
# This is not a style preference. The interface runs for hours against one process, and a value
# fixed at import cannot be changed without restarting it -- which would make ``reload_tuning`` a
# half-truth, taking effect for the per-stage overrides and silently not for the model, the tiers,
# the concurrency cap or whether redaction is on. Half a configuration reloading is worse than
# none, because the half that does not is invisible.
#
# The ``_BUILTIN_*`` values are the genuine constants: what applies when nothing is configured
# anywhere. Those are fixed at import because they are fixed, full stop.

_BUILTIN_MODEL_ID = "gemini-2.5-pro"
_BUILTIN_TEMPERATURE = 0.3
_BUILTIN_MAX_ATTEMPTS = 4
_BUILTIN_MAX_IMAGE_BYTES = 4_000_000
_BUILTIN_MAX_CORPUS_CHARS = 2_000_000
_BUILTIN_MAX_CONTEXT_CHARS = 400_000
_BUILTIN_CONCURRENCY = 4
_BUILTIN_RESOLVE_PASSES = 2


def llm_model_id() -> str:
    """The model every tier falls back to. A name SafeChain knows -- a key in the shared
    config.yml, not a provider's own path-style identifier."""
    return os.getenv("LLM_MODEL_ID", "").strip() or _BUILTIN_MODEL_ID


def llm_vision() -> bool:
    """Whether the configured model accepts images alongside text. Set LLM_VISION=off where the
    gateway rejects the multimodal request shape -- diagram reading then degrades to asking a
    person to describe the flow, rather than the run failing."""
    return _truthy(setting("LLM_VISION", "ingestion", "vision", default="on"))


def max_image_bytes() -> int:
    """Base64 inflates an image by about a third, and gateways cap the request body. Anything
    larger is refused with a reason rather than sent and rejected."""
    return setting("LLM_MAX_IMAGE_BYTES", "ingestion", "max_image_bytes",
                   default=_BUILTIN_MAX_IMAGE_BYTES, cast=int)


def default_temperature() -> float:
    return setting("LLM_TEMPERATURE", "defaults", "temperature",
                   default=_BUILTIN_TEMPERATURE, cast=float)


# --------------------------------------------------------------------------- redaction
#
# Submitted documents routinely carry names, account numbers and other business-sensitive
# material that has no reason to reach a model call. Every segment ingestion reads a document
# into is redacted before it is joined into a corpus -- see ingest/redaction.py, which is the
# only place these are read. Off by default so the tool runs unmodified wherever the internal
# redaction package (pii-redactor, built on ee_utils.redaction) has not been installed; turn it
# on once it has, for anything but a local run against material that was never sensitive.
def pii_redaction() -> bool:
    return _truthy(setting("PII_REDACTION", "redaction", "enabled", default="off"))


def pii_redaction_mode() -> str:
    """masking | substitution | disabled."""
    return setting("PII_REDACTION_MODE", "redaction", "mode", default="masking")


def pii_redaction_replacement_text() -> str:
    return setting("PII_REDACTION_REPLACEMENT_TEXT", "redaction",
                   "replacement_text", default="[REDACTED]")


def pii_redaction_sensitivity():
    """strict | balanced | loose -- how aggressive the engine's fallback masking is. Left unset
    (rather than defaulted here) so the engine's own default applies unless a value is given."""
    return setting("PII_REDACTION_SENSITIVITY", "redaction", "sensitivity", default=None)


def _csv(raw) -> list:
    """A comma-separated string or an already-a-list from the tuning file, either way a list."""
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def pii_redaction_exclude_entities() -> list:
    """Detector labels to switch off, comma-separated."""
    return _csv(setting("PII_REDACTION_EXCLUDE_ENTITIES", "redaction",
                        "exclude_entities", default=""))


def pii_redaction_allow() -> list:
    """Terms to leave alone regardless of what the engine would otherwise mask, comma-separated --
    e.g. PII_REDACTION_ALLOW=American Express,New York."""
    return _csv(setting("PII_REDACTION_ALLOW", "redaction", "allow", default=""))


def _thresholds(raw) -> dict:
    """``name=score,name=score`` from the environment, as the ``{"name": {"score": score}}``
    shape the engine expects -- e.g. PII_REDACTION_THRESHOLDS=secondary_pii_email=0.7."""
    if isinstance(raw, dict):
        return {str(k): {"score": float(v)} for k, v in raw.items()}
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


def pii_redaction_thresholds() -> dict:
    return _thresholds(setting("PII_REDACTION_THRESHOLDS", "redaction", "thresholds", default=""))


def default_max_attempts() -> int:
    """How many times a call is retried before it is reported as failed, where a tier does not set
    its own. Retries cover a gateway that is busy or briefly unreachable; anything the model
    rejects on its merits is raised, since repeating a malformed request only wastes the time it
    takes to fail again. A tier under heavier concurrent load -- more chunks in flight, each one
    retrying -- can turn a brief gateway hiccup into a pile of simultaneous retries, which is what
    the per-tier override exists to relieve independently of this default."""
    return setting("LLM_MAX_ATTEMPTS", "defaults", "max_attempts",
                   default=_BUILTIN_MAX_ATTEMPTS, cast=int)


@dataclass(frozen=True)
class Tier:
    """A model, an output cap, a reasoning effort, a temperature and a retry count, chosen
    together for one kind of work."""

    name: str
    model: str
    max_tokens: int
    reasoning_effort: str
    max_attempts: int = _BUILTIN_MAX_ATTEMPTS
    temperature: float = None


def _tier(name: str, prefix: str, max_tokens: int, effort: str,
          max_attempts: int = None) -> Tier:
    """Build a tier from the environment, then ``tuning.yml``, then these built-in defaults."""
    return Tier(
        name=name,
        model=setting(f"{prefix}_MODEL_ID", "tiers", name, "model", default="") or llm_model_id(),
        max_tokens=setting(f"{prefix}_MAX_TOKENS", "tiers", name, "max_tokens",
                           default=max_tokens, cast=int),
        reasoning_effort=setting(f"{prefix}_REASONING_EFFORT", "tiers", name, "reasoning_effort",
                                 default=effort),
        max_attempts=setting(f"{prefix}_MAX_ATTEMPTS", "tiers", name, "max_attempts",
                             default=max_attempts or default_max_attempts(), cast=int),
        temperature=setting(f"{prefix}_TEMPERATURE", "tiers", name, "temperature",
                            default=default_temperature(), cast=float),
    )


# Reading documents, drafting the intake, weighing materiality, reviewing the scenario space. These
# read a lot, reason at length, and must justify what they conclude.
#
# The output cap is the model's own ceiling rather than half of it. Reasoning tokens are drawn
# from the same budget as the reply, so a high effort against a small cap truncates the JSON
# instead of shortening the answer -- which is why the cap and the effort are set together and
# why the cap is generous.
#
# 65,536 is 2^16, which is why it reads as oddly precise beside the round decimals below. It is
# not a tuned figure: it is the ceiling the gateway actually enforces. Asking for more comes back
# as "supported range is from 1 (inclusive) to 65537 (exclusive)", so there is nothing above this
# to raise it to. Lower it only if a model you point a tier at accepts less.
def judgement() -> Tier:
    return _tier("judgement", "LLM_JUDGEMENT", 65_536, "high")

# Weighing materiality. Judgement-shaped work -- it reasons about a scenario against its peers --
# but unlike drafting or review it runs as many concurrent chunk calls as the scenario space has
# chunks, all against the same tier at once. That concurrency is what turns an ordinary gateway
# hiccup into a pile of simultaneous retries, so this tier gets its own retry ladder, shorter than
# the default, and its own model setting, separate from JUDGEMENT, so it can be pointed at
# something smaller without changing what drafting or review use.
def materiality() -> Tier:
    return _tier("materiality", "LLM_MATERIALITY", 32_000, "medium", max_attempts=2)

# Writing each scenario up for the model owner. Mechanical and bounded by the batch size, but
# the text is issued and read by people, so it stays on the main model by default.
def standard() -> Tier:
    return _tier("standard", "LLM", 16_000, "minimal")


# --------------------------------------------------------------------------- per-stage overrides
#
# A tier is shared by every call doing the same *kind* of work, and that is coarse on purpose --
# pointing MATERIALITY at a smaller model changes every materiality-shaped judgement at once,
# which is usually what is wanted. Sometimes it is not: weighing a scenario's materiality and
# checking the review's declared category are both MATERIALITY-tier work, but they are two
# different calls in two different passes, run at two different points in the pipeline, and a
# scenario space can want them tuned differently -- a smaller model for the mechanical category check,
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
    "INGEST_DIAGRAM_REPAIR", "INTAKE_DRAFT", "INTAKE_REPAIR", "STRUCTURE_REVIEW", "WRITER",
    "MATERIALITY_ASSESS",
    "REVIEWER_ASSESS", "REVIEWER_CATEGORY", "REVIEWER_PROPOSE",
    "COVERAGE_MIGRATE", "COVERAGE_MAP",
)


def stage_tier(stage: str, base: Tier) -> Tier:
    """``base`` with any override for this one call site applied, field by field.

    Read at the point of use rather than built once, the same reason :func:`max_corpus_chars` is
    a function and not a constant: the interface runs for hours, and a value fixed at import
    cannot be changed without restarting it.
    """
    key, prefix = stage.lower(), f"LLM_STAGE_{stage.upper()}"
    return Tier(
        name=f"{base.name}:{key}",
        model=setting(f"{prefix}_MODEL_ID", "stages", key, "model", default="") or base.model,
        max_tokens=setting(f"{prefix}_MAX_TOKENS", "stages", key, "max_tokens",
                           default=base.max_tokens, cast=int),
        reasoning_effort=setting(f"{prefix}_REASONING_EFFORT", "stages", key, "reasoning_effort",
                                 default=base.reasoning_effort),
        max_attempts=setting(f"{prefix}_MAX_ATTEMPTS", "stages", key, "max_attempts",
                             default=base.max_attempts, cast=int),
        temperature=setting(f"{prefix}_TEMPERATURE", "stages", key, "temperature",
                            default=base.temperature, cast=float),
    )


def stage_batch_size(stage: str, default: int) -> int:
    """How many items one call in ``stage`` covers, or ``default`` where nothing overrides it."""
    return setting(f"LLM_STAGE_{stage.upper()}_BATCH_SIZE", "stages", stage.lower(),
                   "batch_size", default=default, cast=int)


# How much submitted text goes into one reading call. Gemini 2.5 Pro takes about a million tokens
# of input; four characters to a token puts this near half a million tokens, which leaves ample
# room for the prompt and the reply. A pack larger than this is split across calls, which reads
# worse, so the number is set to make that rare.
def max_corpus_chars() -> int:
    """The corpus limit, read at the point of use.

    A function rather than a constant because the interface runs for hours and a value fixed at
    import cannot be changed without a restart.
    """
    return setting("LLM_MAX_CORPUS_CHARS", "ingestion", "max_corpus_chars",
                   default=_BUILTIN_MAX_CORPUS_CHARS, cast=int)


# How many times a question the first reading left open is put back to the documents before it is
# put to the model owner. Each pass is one call over the whole corpus, and each one that lands
# saves the model owner a question. The last pass also triages what is left: a question only a
# person can answer is worth asking, and one that does not change what gets tested is not worth
# anyone's time.
# How much supplementary context one call carries: the reading of the documents plus every note.
# Generous on purpose -- roughly 100k tokens against models that hold a million -- because the
# alternative to a cap that is never hit is a cap that quietly drops part of the reading on every
# run. What happens when it *is* hit is in core.context: whole sections, from the end, said aloud.
def max_context_chars() -> int:
    return setting("LLM_MAX_CONTEXT_CHARS", "ingestion", "max_context_chars",
                   default=_BUILTIN_MAX_CONTEXT_CHARS, cast=int)


def ingest_resolve_passes() -> int:
    return setting("LLM_INGEST_RESOLVE_PASSES", "ingestion", "resolve_passes",
                   default=_BUILTIN_RESOLVE_PASSES, cast=int)

# Batching is two separate numbers, and it is easy to conflate them. "Batch size" (stage_batch_size,
# above) is how many rows -- scenarios, chunks of text -- go into the payload of *one* call: it
# decides call *count* for a fixed amount of work, and a bigger batch is fewer, larger calls, not
# faster ones. MAX_CONCURRENCY is how many of those calls are ever in flight to the gateway *at
# once*: it decides call *overlap*, not count, and does not change what any single call is asked to
# judge. Turning batch size down and concurrency up sends more, smaller calls, more of them at
# once, which is usually faster and always cheaper per call to retry; turning batch size up sends
# fewer, larger calls that each risk more work if one of them fails.
def max_concurrency() -> int:
    return setting("LLM_MAX_CONCURRENCY", "concurrency", default=_BUILTIN_CONCURRENCY, cast=int)


def stage_concurrency(stage: str, default: int = None) -> int:
    """How many of one stage's batched calls run at once, from ``LLM_STAGE_<stage>_CONCURRENCY``.

    Falls back to ``default`` where the caller has one worth preferring over the global cap (a
    pass with unusually large individual calls, say), and from there to ``LLM_MAX_CONCURRENCY`` --
    the same stage-then-global shape as :func:`stage_tier`, minus the tier step, since concurrency
    is not a property of a tier.
    """
    fallback = default if default is not None else max_concurrency()
    return setting(f"LLM_STAGE_{stage.upper()}_CONCURRENCY", "stages", stage.lower(),
                   "concurrency", default=fallback, cast=int)


# --------------------------------------------------------------------------- the settled names
#
# Every call site reads ``config.JUDGEMENT``, ``config.PII_REDACTION`` and the rest as attributes,
# which is the right thing for them to read: what a pass wants is "the judgement tier", not "the
# judgement tier as of whenever this module happened to be imported". Answering those names from
# the resolvers above is what makes the second reading the true one, without a single call site
# having to say so.
#
# Nothing in this package imports these names directly (``from .config import JUDGEMENT`` would
# bind once and go stale, which is the very thing this exists to prevent); there is a test that
# says so, in tests/test_settings_surface.py.
_LIVE = {
    "LLM_MODEL_ID": llm_model_id,
    "LLM_VISION": llm_vision,
    "MAX_IMAGE_BYTES": max_image_bytes,
    "DEFAULT_TEMPERATURE": default_temperature,
    "DEFAULT_MAX_ATTEMPTS": default_max_attempts,
    "MAX_CORPUS_CHARS": max_corpus_chars,
    "MAX_CONCURRENCY": max_concurrency,
    "MAX_CONTEXT_CHARS": max_context_chars,
    "INGEST_RESOLVE_PASSES": ingest_resolve_passes,
    "JUDGEMENT": judgement,
    "MATERIALITY": materiality,
    "STANDARD": standard,
    "PII_REDACTION": pii_redaction,
    "PII_REDACTION_MODE": pii_redaction_mode,
    "PII_REDACTION_REPLACEMENT_TEXT": pii_redaction_replacement_text,
    "PII_REDACTION_SENSITIVITY": pii_redaction_sensitivity,
    "PII_REDACTION_EXCLUDE_ENTITIES": pii_redaction_exclude_entities,
    "PII_REDACTION_ALLOW": pii_redaction_allow,
    "PII_REDACTION_THRESHOLDS": pii_redaction_thresholds,
}


def __getattr__(name: str):
    """Resolve a setting at the moment it is read. See :data:`_LIVE`."""
    resolve = _LIVE.get(name)
    if resolve is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return resolve()


def __dir__() -> list:
    return sorted(list(globals()) + list(_LIVE))
