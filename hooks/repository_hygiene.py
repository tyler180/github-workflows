#!/usr/bin/env python3
"""Reject trailing whitespace and merge conflict markers without modifying files."""
import sys
from pathlib import Path


def violations(text):
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.rstrip(" \t") != line:
            result.append((number, "trailing whitespace"))
        if line.startswith(("<<<<<<< ", "=======", ">>>>>>> ")):
            result.append((number, "merge conflict marker"))
    return result


def main(paths):
    failed = False
    for filename in paths:
        path = Path(filename)
        # Unified diffs intentionally contain whitespace-only context lines.
        if path.is_symlink() or path.suffix in (".patch", ".diff"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            print("Cannot read hook input", file=sys.stderr)
            return 1
        for number, reason in violations(text):
            print(f"{filename}:{number}: {reason}")
            failed = True
    return int(failed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
