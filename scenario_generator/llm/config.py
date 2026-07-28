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
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

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


@dataclass(frozen=True)
class Tier:
    """A model, an output cap and a reasoning effort, chosen together for one kind of work."""

    name: str
    model: str
    max_tokens: int
    reasoning_effort: str


def _tier(name: str, prefix: str, max_tokens: int, effort: str) -> Tier:
    """Build a tier from the environment, falling back to the main model where none is set."""
    return Tier(
        name=name,
        model=os.getenv(f"{prefix}_MODEL_ID", "").strip() or LLM_MODEL_ID,
        max_tokens=int(os.getenv(f"{prefix}_MAX_TOKENS", str(max_tokens))),
        reasoning_effort=os.getenv(f"{prefix}_REASONING_EFFORT", effort),
    )


# Reading documents, drafting the intake, weighing materiality, reviewing the benchmark. These
# read a lot, reason at length, and must justify what they conclude.
#
# The output cap is the model's own ceiling rather than half of it. Reasoning tokens are drawn
# from the same budget as the reply, so a high effort against a small cap truncates the JSON
# instead of shortening the answer -- which is why the cap and the effort are set together and
# why the cap is generous.
JUDGEMENT = _tier("judgement", "LLM_JUDGEMENT", 65_536, "high")

# Writing each scenario up for the modelling team. Mechanical and bounded by the batch size, but
# the text is issued and read by people, so it stays on the main model by default.
STANDARD = _tier("standard", "LLM", 16_000, "minimal")

# Mapping the modelling team's free-text scenarios onto the intake's declared vocabulary. This is
# classification against a closed list, with validation afterwards discarding anything outside
# it -- the cheapest useful model that can follow the format is enough. Set LLM_FAST_MODEL_ID to
# point it somewhere smaller.
FAST = _tier("fast", "LLM_FAST", 8_000, "minimal")

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

# How many questions are put in front of a person at once. The rest are kept and shown on request
# rather than thrown away, but a list long enough to be daunting is a list nobody works through,
# and the questions that mattered are lost among the ones that did not.
MAX_OPEN_QUESTIONS = int(os.getenv("LLM_MAX_OPEN_QUESTIONS", "6"))

# Kept for callers that still read the older names.
DEFAULT_MAX_TOKENS = STANDARD.max_tokens
JUDGEMENT_MAX_TOKENS = JUDGEMENT.max_tokens
REASONING_EFFORT = STANDARD.reasoning_effort
JUDGEMENT_REASONING_EFFORT = JUDGEMENT.reasoning_effort
