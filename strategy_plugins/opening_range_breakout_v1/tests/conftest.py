"""Import bootstrap for the src-layout package and the repository root.

The plan-mandated src/ tree (GROUP_B_SEMICONDUCTOR_PLAN.md section 9) is not a
uv workspace member, so these tests put ``<package>/src`` and the repository
root on ``sys.path`` explicitly.  Central tooling configuration is deliberately
left untouched: the root pytest ``testpaths`` keeps governing the central
suite, and this package's tests are run only when explicitly targeted.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[3]

for _entry in (str(_PACKAGE_ROOT / "src"), str(_REPO_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
