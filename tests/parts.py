import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTS = {
    "cli": {
        "update": ["test_update"],
        "engine": ["test_engine"],
        "install": ["test_cloud_setup", "test_dsh", "test_install", "test_install_add", "test_install_names",
                    "test_install_rename"],
        "other": None,
    },
}


def modules(package, part):
    parts = PARTS[package]
    found = sorted(path.stem for path in (ROOT / "tests" / package).glob("test_*.py"))
    named = [name for names in parts.values() if names for name in names]
    unknown = sorted(set(named) - set(found))
    if unknown or len(named) != len(set(named)):
        raise SystemExit(f"tests/parts.py: unknown or repeated modules for {package}: {unknown or named}")
    if parts[part] is not None:
        return parts[part]
    return [name for name in found if name not in named]


def main(argv):
    if len(argv) != 2 or argv[0] not in PARTS or argv[1] not in PARTS[argv[0]]:
        choices = "; ".join(f"{package} {'|'.join(parts)}" for package, parts in PARTS.items())
        raise SystemExit(f"usage: python3 -m tests.parts <package> <part> ({choices})")
    package, part = argv
    names = [f"tests.{package}.{name}" for name in modules(package, part)]
    print(f"{package} {part}: {' '.join(names)}", flush=True)
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromNames(names))
    return 0 if result.wasSuccessful() and result.testsRun else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
