"""Report which third-party packages this project needs are reachable from the configured index.

Run this before adding a dependency. In an environment served by an internal mirror rather than
PyPI directly, a package being unavailable is a design constraint rather than an inconvenience --
it decides which implementation gets built, so it is worth knowing before the code is written.

    python tools/check_packages.py

Nothing is installed and nothing is downloaded. The script asks the index which versions exist,
using whichever index pip is already configured to use.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Dict, List, Tuple

# Grouped by what the package would be for, so a gap points at the decision it affects rather
# than just a missing name.
CANDIDATES: Dict[str, List[Tuple[str, str]]] = {
    "In use today": [
        ("openpyxl", "reading and writing every workbook"),
        ("safechain", "model calls, authentication and token refresh"),
        ("langchain_core", "building each call as a prompt piped into a model"),
        ("python-dotenv", "loading .env"),
        ("PyYAML", "the probe library"),
    ],
    "Document ingestion (stage 0)": [
        ("pypdf", "PDF text and page numbers; pure Python, first choice"),
        ("pdfplumber", "alternative PDF reader with better layout fidelity"),
        ("python-docx", "Word documents"),
        ("python-pptx", "slide decks"),
        ("Pillow", "reading and resizing submitted diagrams"),
    ],
    "Local web interface": [
        ("flask", "server; smallest option, bundles Jinja2"),
        ("fastapi", "server; alternative, needs uvicorn as well"),
        ("uvicorn", "only needed if FastAPI is chosen"),
        ("jinja2", "HTML templating; bundled with Flask"),
        ("python-multipart", "file uploads; only needed with FastAPI"),
    ],
}


def available(package: str) -> Tuple[bool, str]:
    """Ask the configured index for a package's versions. Returns (found, newest or reason)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "index", "versions", package],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not ask the index ({exc})"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, detail[-1][:110] if detail else "not found"

    first = (result.stdout or "").strip().splitlines()
    return True, first[0][:110] if first else "available"


def main() -> int:
    missing = []
    for heading, packages in CANDIDATES.items():
        print(f"\n{heading}")
        print("-" * len(heading))
        for package, purpose in packages:
            found, detail = available(package)
            print(f"  {'OK     ' if found else 'MISSING'}  {package:<18} {purpose}")
            if not found:
                missing.append((package, detail))

    print()
    if missing:
        print("Not reachable from this index:")
        for package, detail in missing:
            print(f"  {package}: {detail}")
        print("\nReport these back before the affected component is built -- each one changes an "
              "implementation choice rather than merely needing a workaround.")
    else:
        print("Everything checked is reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
