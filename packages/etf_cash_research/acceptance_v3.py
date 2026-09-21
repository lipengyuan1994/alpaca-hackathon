"""Executable acceptance evidence, never a manually trusted status flag."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import atomic_json, file_hash

from .protocol_v3 import PROTOCOL

ROOT = Path(__file__).resolve().parents[2]


def source_hashes() -> dict[str,str]:
    paths = list((ROOT/'packages/etf_cash_research').glob('*.py'))
    paths += list((ROOT/'tests/etf_cash_research').glob('*v3*.py'))
    paths += [ROOT/'configs/etf_cash_research_v3.yaml',ROOT/'configs/etf_cash_action_resolutions_v3.json',ROOT/'uv.lock',ROOT/'packages/contracts/canonical.py',ROOT/'packages/research_data/artifacts.py']
    return {str(p.relative_to(ROOT)):file_hash(p) for p in sorted(paths)}


def run_acceptance(output: Path) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError('V3_ACCEPTANCE_OUTPUT_NOT_EMPTY')
    if platform.machine()!='arm64' or sys.version_info[:2]!=(3,12):
        raise ValueError('V3_NATIVE_PYTHON_312_REQUIRED')
    output.mkdir(parents=True,exist_ok=True)
    from .runtime_v3 import audit_runtime
    runtime = audit_runtime()
    atomic_json(output/'runtime_audit.json',runtime)
    before = source_hashes()
    command = [sys.executable,'-m','pytest','tests/etf_cash_research','-q','-o','addopts=',f'--junitxml={output.resolve()/"junit.xml"}']
    result = subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    (output/'pytest.log').write_text(result.stdout+result.stderr)
    cases = []
    if (output/'junit.xml').is_file():
        cases = list(ET.parse(output/'junit.xml').getroot().iter('testcase'))
    modules = {c.attrib.get('classname','').split('.')[-1] for c in cases}
    required = {'test_engine_v3','test_data_v3','test_strategies_v3','test_strategies_sa_v3','test_strategies_l_v3','test_independent_reconstruction_v3'}
    failures = sum(bool(list(c.iter('failure'))) or bool(list(c.iter('error'))) or bool(list(c.iter('skipped'))) for c in cases)
    passed = result.returncode==0 and failures==0 and required.issubset(modules) and before==source_hashes()
    evidence = {'schema_version':'etf-engine-acceptance/v3','status':'PASS' if passed else 'BLOCKED','python':sys.version,'architecture':platform.machine(),'protocol_hash':PROTOCOL.protocol_hash,'source_hashes':before,'test_count':len(cases),'test_modules':sorted(modules),'required_modules_missing':sorted(required-modules),'failures_or_skips':failures,'exit_code':result.returncode,'junit_hash':file_hash(output/'junit.xml') if cases else None,'log_hash':file_hash(output/'pytest.log')}
    evidence['acceptance_hash'] = canonical_hash(evidence)
    atomic_json(output/'engine_acceptance.json',evidence)
    return evidence


def verify_acceptance(path: Path) -> dict:
    value = json.loads(path.read_text())
    expected = canonical_hash({k:v for k,v in value.items() if k!='acceptance_hash'})
    if value.get('status')!='PASS' or value.get('acceptance_hash')!=expected or value.get('source_hashes')!=source_hashes() or value.get('protocol_hash')!=PROTOCOL.protocol_hash:
        raise ValueError('V3_ACCEPTANCE_MISSING_FAILED_OR_STALE')
    if file_hash(path.parent/'junit.xml')!=value.get('junit_hash') or file_hash(path.parent/'pytest.log')!=value.get('log_hash'):
        raise ValueError('V3_ACCEPTANCE_TEST_EVIDENCE_ALTERED')
    return value
