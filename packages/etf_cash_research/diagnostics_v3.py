"""Predefined leadership summaries and hypothetical path stress diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data_v3 import FeatureStore
from .protocol_v3 import candidate_registry
from .study_v3 import save_json


def leadership_diagnostics(study,store):
    rows = []
    for candidate in candidate_registry():
        universe = candidate.universe
        broad,sector = (universe.broad_proxy,universe.semiconductor_proxy) if universe.track_id=='b' else universe.tradable_symbols
        path = Path(study)/'runs'/candidate.candidate_id/'continuous/base/equity.parquet'
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        history = pd.DataFrame({'b':store.frames[broad].r126,'s':store.frames[sector].r126}).shift(1)
        history['date'] = history.index
        merged = frame.assign(date=pd.to_datetime(frame.date,utc=True)).merge(history.reset_index(drop=True),on='date',how='left')
        for name,mask in [('broad_leadership',(merged.b>0)&(merged.s<merged.b)),('sector_leadership',(merged.s>0)&(merged.s>merged.b)),('joint_weakness',(merged.b<0)&(merged.s<0))]:
            part = merged[mask]
            rows.append({'candidate_id':candidate.candidate_id,'regime':name,'sessions':len(part),'mean_daily_return':float(part.daily_return.mean()) if len(part) else None,'dollar_profit_contribution':float(part.daily_profit.sum()),'average_invested_exposure':float(part.invested_exposure.mean()) if len(part) else None,'interpretation':'conditional_descriptive_subset_not_independent_tradable_account'})
    pd.DataFrame(rows).to_csv(Path(study)/'leadership_diagnostics.csv',index=False)
    return rows


def hypothetical_inputs(scenario):
    """Deterministic price paths, explicitly not forecasts or observed funds."""
    count = 430
    dates = pd.bdate_range('2020-01-02',periods=count,tz='UTC')
    index = np.arange(count)
    broad = np.full(count,.001)
    sector = np.full(count,.0015)
    if scenario=='broad_up_sector_flat':
        sector[250:] = 0
    elif scenario=='broad_up_sector_down':
        sector[250:] = -.002
    elif scenario=='sector_resurgence':
        sector[250:330] = -.002
        sector[330:] = .004
    elif scenario=='alternating_chop':
        broad[250:] = np.where(index[250:]%2,.02,-.02)
        sector[250:] = np.where(index[250:]%2,.03,-.03)
    elif scenario=='sudden_gap':
        broad[300],sector[300] = -.12,-.20
    else:
        raise ValueError('UNKNOWN_HYPOTHETICAL_SCENARIO')
    rows = []
    for symbol,returns in [('QQQM',broad),('QQQ',broad),('SPY',broad),('SMH',sector),('SOXX',sector),('TQQQ',3*broad),('SPXL',3*broad),('SOXL',3*sector)]:
        close = 100*np.cumprod(1+returns)
        previous = np.r_[100,close[:-1]]
        opening = close.copy() if scenario=='sudden_gap' else previous
        for i,date in enumerate(dates):
            rows.append({'date':date,'symbol':symbol,'open':opening[i],'high':max(opening[i],close[i])*1.001,'low':min(opening[i],close[i])*.999,'close':close[i],'volume':1000000,'split_factor':1.,'dividend':0.,'dividend_payable_date':pd.NaT})
    calendar = pd.DataFrame({'date':dates,'settlement_date':dates+pd.offsets.BDay(2)})
    return pd.DataFrame(rows),calendar,dates[250],dates[-1]


def run_hypothetical_diagnostics(study):
    from types import SimpleNamespace

    from .engine_v3 import EngineConfig, run_engine_v3
    from .strategies_v3 import make_strategy
    from .study_v3 import feature_cache
    rows = []
    for scenario in ('broad_up_sector_flat','broad_up_sector_down','sector_resurgence','alternating_chop','sudden_gap'):
        bars,calendar,start,end = hypothetical_inputs(scenario)
        data = SimpleNamespace(bars=bars,calendar=calendar,actions=pd.DataFrame())
        store = FeatureStore(bars)
        cache = feature_cache(store)
        for candidate in candidate_registry():
            config = EngineConfig(cost_basis_points=10 if candidate.universe.track_id=='b' else 5,include_history=False,feature_cache=cache)
            strategy = make_strategy(candidate,store=store,data=data,config=config,start=start,end=end)
            result = run_engine_v3(bars,strategy=strategy,config=config,calendar=calendar,tradable_symbols=candidate.universe.tradable_symbols,start=start,end=end,candidate_id=candidate.candidate_id)
            values = result.equity.equity
            rows.append({'candidate_id':candidate.candidate_id,'scenario':scenario,'net_return':float(values.iloc[-1]/1000-1),'max_drawdown':float((1-values/values.cummax().clip(lower=1000)).max()),'label':'HYPOTHETICAL_FIXTURE_NOT_HISTORICAL_FUND_RETURN_OR_FORECAST'})
    pd.DataFrame(rows).to_csv(Path(study)/'hypothetical_stress.csv',index=False)
    return rows


def migration_comparison(study):
    old = Path(__file__).resolve().parents[2]/'output/etf_cash_research_v2/20260920_corrected_calculations'
    old_evidence = json.loads((old/'migration_comparison.json').read_text()) if (old/'migration_comparison.json').exists() else {'rows':[]}
    results = []
    for strategy in ('S01','S10'):
        cid = f'{strategy}__QQQM_SMH__primary'
        current = Path(study)/'runs'/cid/'continuous/base/metrics.json'
        if not current.exists():
            continue
        results.append({'candidate_id':cid,'new_metrics':json.loads(current.read_text()),'prior_reference_root':str(old),'prior_aligned_metrics':next((x['aligned_v2_actual_raw_execution'] for x in old_evidence['rows'] if x['strategy_id']==strategy and x['cost_scenario']=='base'),None),'migration_causes':['previous_close_quantity_sizing','execution_calendar_reviews','fill_based_age_and_cooldown','Wilder_indicator_initialization','complete_shadow_and_component_ownership','verified_dividend_resolution'],'original_selection_preserved':True})
    save_json(Path(study)/'migration_comparison.json',{'comparisons':results,'status':'SEMANTICS_CHANGED_NEW_RUNS_REQUIRED'})


def frictionless_references(study,data):
    parts = []
    for _symbol,group in data.bars.groupby('symbol'):
        group = group.sort_values('date').copy()
        group['daily_return'] = ((group.close+group.dividend)*group.split_factor/group.close.shift())-1
        selected = group[group.date.between(pd.Timestamp('2023-09-19',tz='UTC'),pd.Timestamp('2026-09-18',tz='UTC'))][['date','symbol','daily_return']].copy()
        selected['total_return_index'] = 1000*(1+selected.daily_return).cumprod()
        selected['label'] = 'frictionless_close_to_close_dividend_reinvestment_reference_not_cash_account'
        parts.append(selected)
    pd.concat(parts,ignore_index=True).to_parquet(Path(study)/'frictionless_references.parquet',index=False)
