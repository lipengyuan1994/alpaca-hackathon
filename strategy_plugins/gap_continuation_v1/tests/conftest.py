"""Make the src-layout package and the repository root importable for tests.

The wheel-installed layout is not assumed; tests run against the in-repo
``src/`` package plus the central ``packages/`` tree exactly as the frozen
integration contract prescribes.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[3]
for _entry in (str(_PACKAGE_ROOT / "src"), str(_REPO_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
