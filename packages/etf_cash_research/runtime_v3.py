"""Native-runtime evidence for local ETF research."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

import numpy
import pandas
import pyarrow


def audit_runtime():
    manager = Path('/opt/homebrew/bin/uv')
    output = subprocess.check_output([str(manager),'--version'],text=True).strip()
    manager_arch = subprocess.check_output(['/usr/bin/file',str(manager.resolve())],text=True).strip()
    if platform.machine()!='arm64' or sys.version_info[:2]!=(3,12) or 'arm64' not in manager_arch:
        raise ValueError('V3_NATIVE_RUNTIME_REQUIRED')
    binaries = []
    for module in (numpy,pandas,pyarrow):
        for file in sorted(Path(module.__file__).parent.rglob('*.so')):
            detail = subprocess.check_output(['/usr/bin/file',str(file)],text=True).strip()
            if 'arm64' not in detail:
                raise ValueError(f'V3_NON_NATIVE_EXTENSION:{file}')
            binaries.append(detail)
    return {'manager':str(manager),'manager_version':output,'manager_architecture':manager_arch,'python':str(Path(sys.executable).resolve()),'machine':platform.machine(),'version':sys.version,'extensions_checked':len(binaries),'extensions':binaries}
