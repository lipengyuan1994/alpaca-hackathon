"""Version-specific CLI dispatch; v1/v2 command behavior remains unchanged."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .protocol_v3 import StudyProtocolV3


def dispatch(argv: list[str] | None) -> int | None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return None
    command = argv[0]
    if command not in {'audit-engine','run-study','rank-study','report-study','reproduce-study','archive-study'}:
        return None
    def option(name):
        return Path(argv[argv.index(name)+1]) if name in argv and argv.index(name)+1<len(argv) else None
    protocol = option('--protocol')
    study = option('--study')
    probe = protocol or (study/'protocol.json' if study else None)
    if command not in {'report-study','archive-study'}:
        if not probe or not probe.is_file() or yaml.safe_load(probe.read_text()).get('schema_version')!='etf-cash-research-protocol/v3':
            return None
    parser = argparse.ArgumentParser(prog=f'etf-cash-research {command}')
    parser.add_argument('--protocol',type=Path)
    if command=='audit-engine':
        parser.add_argument('--output',type=Path,required=True)
    elif command=='run-study':
        parser.add_argument('--data-manifest',type=Path,required=True)
        parser.add_argument('--acceptance',type=Path,required=True)
        parser.add_argument('--output',type=Path,required=True)
        parser.add_argument('--family',choices=['all','S','A','L'],default='all')
        parser.add_argument('--pair',choices=['QQQM_SOXX','QQQM_SMH','TQQQ_SOXL','SPXL_SOXL'])
        parser.add_argument('--resume',action='store_true')
    else:
        parser.add_argument('--study',type=Path,required=True)
        if command in {'report-study','reproduce-study'}:
            parser.add_argument('--output',type=Path,required=True)
        if command=='reproduce-study':
            parser.add_argument('--resume',action='store_true')
    args = parser.parse_args(argv[1:])
    if args.protocol:
        StudyProtocolV3.from_yaml(args.protocol)
    if command=='audit-engine':
        from .acceptance_v3 import run_acceptance
        result = run_acceptance(args.output)
        print(json.dumps(result,sort_keys=True))
        return 0 if result['status']=='PASS' else 1
    from . import study_v3
    if command=='run-study':
        result = study_v3.run_study(args.data_manifest,args.acceptance,args.output,family=args.family,pair=args.pair,resume=args.resume)
    elif command=='rank-study':
        result = study_v3.rank_study(args.study)
    elif command=='report-study':
        from .report_v3 import build_report
        result = build_report(args.study,args.output)
    elif command=='archive-study':
        result = study_v3.archive_study(args.study)
    else:
        result = study_v3.reproduce_study(args.study,args.output,resume=args.resume)
    print(result)
    return 0
