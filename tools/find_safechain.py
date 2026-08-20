"""Report what the installed SafeChain exposes, and which interpreter is being used.

Run this when a model call fails with "could not find SafeChain's model factory", or when it is
not clear which of several Pythons on the machine is the one being used:

    python tools/find_safechain.py

It answers three questions in order, because they are the three things that go wrong and each one
makes the next one moot: which interpreter is running, whether SafeChain is importable from it,
and what the factory is called inside it.
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
from pathlib import Path

# Run as `python tools/find_safechain.py`, the directory on the path is tools/ rather than the
# project. Adding the project means this works before anything has been installed, which is when
# it is most likely to be needed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _interpreter() -> None:
    print("Interpreter")
    print("-----------")
    print(f"  {sys.executable}")
    print(f"  Python {sys.version.split()[0]}")

    inside = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print(f"  Virtual environment: {'yes, ' + sys.prefix if inside else 'no'}")
    if sys.version_info < (3, 12):
        print("  ! SafeChain needs Python 3.12. This is not it.")
    if not inside:
        print("  ! Not in a virtual environment, so this sees only what was installed globally.")
    print()


def _safechain():
    print("SafeChain")
    print("---------")
    try:
        package = importlib.import_module("safechain")
    except ImportError as exc:
        print(f"  Not importable from this interpreter ({exc}).")
        print("  Install it from the internal index, into the interpreter above.")
        return None

    print(f"  Installed at {getattr(package, '__file__', 'an unknown location')}")
    version = getattr(package, "__version__", "")
    if version:
        print(f"  Version {version}")

    submodules = sorted(m.name for m in pkgutil.iter_modules(getattr(package, "__path__", [])))
    print(f"  Submodules: {', '.join(submodules) or 'none'}")
    print()
    return package


def _factory(package) -> None:
    """Find anything callable that looks like the model factory, and name its import path."""
    from metric.llm.gateway import FACTORY_CANDIDATES, FACTORY_ENV

    print("The model factory")
    print("-----------------")

    override = os.getenv(FACTORY_ENV, "").strip()
    if override:
        print(f"  {FACTORY_ENV} is set to {override}")

    found = []
    for candidate in FACTORY_CANDIDATES:
        module_name, _, attribute = candidate.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if callable(getattr(module, attribute, None)):
            found.append(candidate)

    if found:
        print(f"  Found at: {found[0]}")
        for extra in found[1:]:
            print(f"            {extra}  (also present)")
        print("  Nothing to configure — this is one of the paths already tried.")
        return

    # Nothing matched, so sweep for it and report the path to put in the environment.
    print("  None of the paths this checks were found. Sweeping for anything callable named")
    print("  'model' inside safechain:\n")

    seen = False
    for finder in pkgutil.walk_packages(getattr(package, "__path__", []), "safechain."):
        try:
            module = importlib.import_module(finder.name)
        except Exception:                                  # a submodule that will not import
            continue
        for name in dir(module):
            if "model" in name.lower() and callable(getattr(module, name, None)):
                print(f"    {finder.name}:{name}")
                seen = True

    if not seen:
        print("    nothing found — check the SafeChain onboarding notebook for the import line.")
    else:
        print(f"\n  Put the right one in .env as {FACTORY_ENV}=module:attribute")


def main() -> int:
    _interpreter()
    package = _safechain()
    if package is not None:
        _factory(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
