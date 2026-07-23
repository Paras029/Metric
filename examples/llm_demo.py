"""Full pipeline against a live gateway. Requires a completed .env (see .env.example).

    python examples/build_claims_intake.py claims_intake.xlsx
    python examples/llm_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # importable from anywhere

from scenario_generator import generate, map_coverage, review

INTAKE = "claims_intake.xlsx"
PREFIX = "claims"
REGISTRY = f"{PREFIX}_registry.xlsx"

# Build the benchmark: graph walk, probes, descriptions, materiality.
generate(INTAKE, PREFIX, with_probes=True)

# Review it as a whole. Updates the registry in place, and may propose additions.
review(INTAKE, REGISTRY, REGISTRY)

# Map an owner's own scenario library onto the benchmark.
# owner.xlsx needs a 'Scenarios' sheet: ID | Description | Decision Path (optional).
map_coverage(INTAKE, REGISTRY, "owner.xlsx", f"{PREFIX}_overlap.xlsx")
