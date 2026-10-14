#!/usr/bin/env python3
"""Optional pre-commit advisory review."""
from pathlib import Path
import runpy
import sys

sys.argv = [str(Path(__file__).resolve().parents[1] / 'scripts/codex-review.py'), '--hook', '--staged']
runpy.run_path(sys.argv[0], run_name='__main__')
