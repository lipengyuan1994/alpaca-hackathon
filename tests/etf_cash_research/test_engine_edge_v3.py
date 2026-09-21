"""Independent integration checks for repaired engine edge semantics."""
import pandas as pd
import pytest

from packages.etf_cash_research.engine_v3 import (
    EngineConfig,
    OrderIntent,
    SizingMode,
    run_engine_v3,
)


def market(closes):
    dates = pd.bdate_range('2024-06-03',periods=len(closes),tz='UTC')
    bars = pd.DataFrame({'date':dates,'symbol':'QQQ','open':closes,'high':closes,'low':closes,'close':closes})
    calendar = pd.DataFrame({'date':dates,'settlement_date':dates+pd.offsets.BDay(1)})
    return bars,calendar,dates


def test_fixed_shares_are_not_trimmed_after_appreciation():
    bars,calendar,dates = market([100,100,1000,2000])
    class Entry:
        def decide(self,context):
            return [OrderIntent('QQQ',SizingMode.ENTER_SLEEVE,target_weight=.99)] if context.initial_session else []
    result = run_engine_v3(bars,strategy=Entry(),calendar=calendar,start=dates[1],config=EngineConfig(cost_basis_points=0))
    assert len(result.fills)==1
    assert result.final_positions['QQQ'].quantity==9.9


def test_context_holding_age_counts_only_completed_sessions():
    bars,calendar,dates = market([100]*5)
    seen = []
    class Entry:
        def decide(self,context):
            position = context.positions.get(('account','QQQ'))
            if position and position.quantity:
                seen.append((context.execution_session,position.holding_sessions))
            return [OrderIntent('QQQ',SizingMode.ENTER_SLEEVE,target_weight=.5)] if context.initial_session else []
    run_engine_v3(bars,strategy=Entry(),calendar=calendar,start=dates[1])
    assert seen==[(dates[2],1),(dates[3],2),(dates[4],3)]


def test_same_symbol_component_sales_pay_one_external_fee():
    bars,calendar,dates = market([100]*5)
    class Components:
        def decide(self,context):
            if context.initial_session:
                return [OrderIntent('QQQ',SizingMode.ENTER_SLEEVE,component_id=c,target_weight=.2) for c in ('a','b')]
            if context.execution_session==dates[2]:
                return [OrderIntent('QQQ',SizingMode.EXIT_FULLY,component_id=c) for c in ('a','b')]
            return []
    result = run_engine_v3(bars,strategy=Components(),calendar=calendar,start=dates[1],config=EngineConfig(cost_basis_points=0))
    assert result.fills.fee.sum()==pytest.approx(.01)
    assert result.equity.equity.iloc[-1]==pytest.approx(999.99)


def test_small_component_buys_are_net_external_order_minimum():
    bars,calendar,dates = market([100]*3)
    class Components:
        def decide(self,context):
            return [OrderIntent('QQQ',SizingMode.ENTER_SLEEVE,component_id=c,target_weight=.003) for c in ('a','b')] if context.initial_session else []
    result = run_engine_v3(bars,strategy=Components(),calendar=calendar,start=dates[1],config=EngineConfig(cost_basis_points=0))
    assert result.final_positions['QQQ'].quantity==pytest.approx(.06)


def test_missing_calendar_session_is_not_silently_removed():
    bars,calendar,dates = market([100]*4)
    bars = bars[~bars.date.eq(dates[2])]
    class Empty:
        def decide(self,context):
            return []
    with pytest.raises(ValueError,match='MISSING|COVERAGE'):
        run_engine_v3(bars,strategy=Empty(),calendar=calendar,start=dates[1])


def test_simultaneous_cancellations_have_stable_order_across_hash_seeds(tmp_path):
    import os
    import subprocess
    import sys
    script = tmp_path/'hash_seed_fixture.py'
    script.write_text('''
import pandas as pd
from packages.etf_cash_research.engine_v3 import EngineConfig,OrderIntent,SizingMode,run_engine_v3
D=pd.bdate_range('2024-06-03',periods=5,tz='UTC')
B=pd.DataFrame([dict(date=d,symbol=s,open=100.,high=100.,low=100.,close=100.) for d in D for s in ('QQQM','SOXX')])
C=pd.DataFrame({'date':D,'settlement_date':D+pd.offsets.BDay(1)})
class S:
 def decide(self,c):
  if c.execution_session==D[1]:return [OrderIntent(s,SizingMode.ENTER_SLEEVE,target_weight=.495) for s in ('QQQM','SOXX')]
  if c.execution_session==D[2]:return [OrderIntent(s,SizingMode.EXIT_FULLY) for s in ('QQQM','SOXX')]
  return []
r=run_engine_v3(B,strategy=S(),calendar=C,start=D[1],config=EngineConfig(execution_delay_sessions=1),tradable_symbols=('QQQM','SOXX'))
print(r.account_hash)
''')
    hashes=[]
    for seed in ('1','2','3','135'):
        env={**os.environ,'PYTHONHASHSEED':seed,'PYTHONPATH':str(__import__('pathlib').Path.cwd())}
        hashes.append(subprocess.check_output([sys.executable,str(script)],env=env,text=True).strip())
    assert len(set(hashes))==1
