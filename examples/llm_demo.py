"""Full pipeline against a live model. Requires a completed .env and a config.yml
(see .env.example).

    python examples/build_claims_intake.py claims_intake.xlsx
    python examples/llm_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # importable from anywhere

from scenario_generator import generate, map_conversation_coverage, review

INTAKE = "claims_intake.xlsx"
PREFIX = "claims"
METADATA = f"{PREFIX}_scenario_space_metadata.xlsx"

# Build the scenario space: graph walk, probes, descriptions, materiality.
generate(INTAKE, PREFIX, with_probes=True)

# Review it as a whole. Updates the scenario space metadata in place, and may propose additions.
review(INTAKE, METADATA, METADATA)

# Count how much of the space the team's own conversations already exercise.
# their_conversations.xlsx holds transcripts in any of the layouts ingest.conversations reads:
# one row per turn with a conversation id, one row per whole transcript, or prose with
# 'User:'/'Agent:' prefixes. threshold is how many conversations a scenario needs before it
# counts as represented.
map_conversation_coverage(INTAKE, METADATA, "their_conversations.xlsx",
                          f"{PREFIX}_coverage.xlsx", threshold=1)
