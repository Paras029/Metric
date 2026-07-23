"""LLM gateway configuration. Everything sensitive comes from the environment (.env)."""
import os

from dotenv import load_dotenv

load_dotenv()

# IDaaS auth
IDAAS_APP_ID = os.getenv("IDAAS_APP_ID", "")
IDAAS_KEY = os.getenv("IDAAS_KEY", "")
IDAAS_URL = os.getenv("IDAAS_URL", "")

# The chat-completions model reached through the gateway.
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "")
LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "google/gemini-2.5-pro")
LLM_SCOPE = os.getenv("LLM_SCOPE", "/genai/google/v1/models/gemini-2.5-pro/**::post")

DEFAULT_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# Two budgets, because the passes do very different work.
#
# Routine passes (writing descriptions, extracting metadata) are mechanical: the answer follows
# fairly directly from the input, so little reasoning is needed and the reply is bounded by the
# batch size.
#
# Judgement passes (materiality, review) weigh a scenario against the whole benchmark and must
# justify the verdict. They need room both to reason and to write. Reasoning tokens are drawn
# from the same budget as the reply, so a high reasoning effort against a small cap truncates
# the JSON rather than producing a shorter answer — these two settings move together.
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16000"))
JUDGEMENT_MAX_TOKENS = int(os.getenv("LLM_JUDGEMENT_MAX_TOKENS", "32000"))

# Set either to "" to omit the field entirely for gateways that reject it.
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "minimal")
JUDGEMENT_REASONING_EFFORT = os.getenv("LLM_JUDGEMENT_REASONING_EFFORT", "high")
