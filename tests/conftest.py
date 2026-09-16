"""Test configuration: make scripts/ importable so tests can `import normalize`.

normalize.py is a stdlib-only script that guards its executable code behind
`if __name__ == '__main__':`, so importing it here has no side effects.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
