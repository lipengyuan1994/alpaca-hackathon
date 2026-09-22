"""Registered study orchestration invariants independent of market outcomes."""

import pandas as pd
import pytest

from packages.etf_cash_research.engine_v3 import EngineConfig, run_engine_v3
from packages.etf_cash_research.protocol_v3 import candidate_registry
from packages.etf_cash_research.study_v3 import Passive, inventory, periods, save_json, verify_run


def test_registered_run_counts_and_stress_dates():
    candidates = candidate_registry()
    assert len(candidates)==63
    assert sum(len(list(periods(c.universe.track_id=='b'))) for c in candidates)==1954
    assert len(list(periods(False)))==30
    assert len(list(periods(True)))==32


def test_run_inventory_rejects_mutation(tmp_path):
    (tmp_path/'evidence.txt').write_text('original')
    save_json(tmp_path/'run_complete.json',{'files':inventory(tmp_path)})
    verify_run(tmp_path)
    (tmp_path/'evidence.txt').write_text('changed')
    with pytest.raises(ValueError,match='HASH_MISMATCH'):
        verify_run(tmp_path)


def test_passive_initial_sizing_uses_previous_close_and_does_not_rebalance():
    dates = pd.date_range('2024-06-03',periods=3,tz='UTC',freq='B')
    bars = pd.DataFrame({'date':dates,'symbol':'QQQ','open':[100,120,150],'high':[100,120,150],'low':[100,120,150],'close':[100,120,150]})
    calendar = pd.DataFrame({'date':dates,'settlement_date':dates+pd.offsets.BDay(1)})
    result = run_engine_v3(bars,strategy=Passive({'QQQ':.5}),calendar=calendar,tradable_symbols=('QQQ',),start=dates[1],end=dates[-1],config=EngineConfig(cost_basis_points=0))
    assert len(result.fills)==1
    assert result.fills.iloc[0].quantity==5
    assert result.equity.iloc[-1].equity==1150


def test_physical_profit_excludes_virtual_transfers_and_reconciles():
    from packages.etf_cash_research.attribution_v3 import physical_profit_attribution
    fills = pd.DataFrame([
        {'date':'2024-06-03T00:00:00Z','symbol':'QQQ','side':'buy','quantity':5.,'price':100.,'fee':0.},
        {'date':'2024-06-04T00:00:00Z','symbol':'QQQ','side':'sell','quantity':2.,'price':110.,'fee':.01},
    ])
    bars = pd.DataFrame({'date':pd.to_datetime(['2024-06-04'],utc=True),'symbol':['QQQ'],'close':[110.]})
    value = physical_profit_attribution(fills,bars,pd.DataFrame(),start='2024-06-03',end='2024-06-04',ending_equity=1049.99,dividends=0)
    assert value['realized_pnl']==pytest.approx(19.99)
    assert value['unrealized_pnl']==30
    assert value['profit_attribution_passed']
