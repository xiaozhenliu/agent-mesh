import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def load_tests(loader, tests, pattern):
    raise RuntimeError("run each test package in its own process; see CONTRIBUTING.md")
