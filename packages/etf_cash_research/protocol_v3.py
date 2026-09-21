"""Frozen registry for the repaired, unified 63-candidate study."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from packages.contracts.canonical import canonical_hash

from .protocol_v2 import CandidateSpec, StudyProtocolV2, UniverseSpec


@dataclass(frozen=True)
class StudyProtocolV3(StudyProtocolV2):
    schema_version: str = 'etf-cash-research-protocol/v3'
    warmup_start: date = date(2019,1,1)

    def as_dict(self):
        value = super().as_dict()
        value.update({
            'primary_candidate_count':63,
            'rules_revision':'approved-2026-09-20-v3',
            'subagent_model':'gpt-5.6-luna',
            'subagent_reasoning_effort':'max',
            'sizing':'prior_close_equity_and_price; split_unit_conversion_only',
            'schedules':'execution_session_calendar; first_session_review',
            'position_state':'actual_fills_and_internal_transfers',
            'quantity_precision':'integer_microshares',
            'delay':'frozen_requested_quantity; one_additional_session',
            'settlement':'verified_provider_calendar_settlement_date',
            'indicators':'Wilder_SMA_seed;sample_volatility;forward_split_consistent',
            'selection_groups':['unleveraged','TQQQ_SOXL','SPXL_SOXL'],
            'sensitivities':False,
            'warmup_policy':'all_available_history_from_2019_or_fund_inception; no_synthetic_preinception_prices',
        })
        return value

    @classmethod
    def from_yaml(cls,path: Path):
        raw = yaml.safe_load(path.read_text())
        if raw.get('schema_version') != cls.schema_version:
            raise ValueError('V3_PROTOCOL_VERSION_REQUIRED')
        expected = cls().as_dict()
        if raw != expected:
            raise ValueError('V3_FROZEN_PROTOCOL_MISMATCH')
        return cls()


PROTOCOL = StudyProtocolV3()


def candidate_registry() -> list[CandidateSpec]:
    output = []
    for semiconductor in ['SOXX','SMH']:
        u = UniverseSpec(f'QQQM_{semiconductor}',('QQQM',semiconductor),track_id='a')
        for n in range(1,11):
            sid = f'S{n:02d}'
            output.append(CandidateSpec(f'{sid}__{u.pair_id}__primary',sid,u))
    u = UniverseSpec('QQQM_SMH',('QQQM','SMH'),track_id='a')
    for n in range(1,12):
        sid = f'A{n:02d}'
        output.append(CandidateSpec(f'{sid}__{u.pair_id}__primary',sid,u))
    for broad,proxy in [('TQQQ','QQQ'),('SPXL','SPY')]:
        u = UniverseSpec(f'{broad}_SOXL',(broad,'SOXL'),(proxy,'SOXX'),proxy,'SOXX','b')
        for n in range(1,17):
            sid = f'L{n:02d}'
            output.append(CandidateSpec(f'{sid}__{u.pair_id}__primary',sid,u))
    assert len(output)==63 and len({c.candidate_id for c in output})==63
    return output


def registry_envelope():
    candidates = [c.as_dict() for c in candidate_registry()]
    return {'schema_version':'etf-cash-registry/v3','candidates':candidates,'registry_hash':canonical_hash(candidates)}
