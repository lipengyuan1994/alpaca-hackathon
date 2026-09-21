"""Immutable, resumable orchestration of the repaired ETF study."""
from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import atomic_json, file_hash

from .acceptance_v3 import ROOT, source_hashes, verify_acceptance
from .data_v3 import FeatureStore, load_market_data
from .engine_v3 import EngineConfig, OrderIntent, SizingMode, run_engine_v3
from .evaluation import chain_evaluation_windows, pooled_paired_bootstrap
from .metrics import add_benchmark_comparison, compute_metrics
from .protocol_v3 import PROTOCOL, candidate_registry, registry_envelope

NOTICE = 'deterministic research backtest; historical periods reused; LLM overlay not performance-tested; no live authorization'
TABLES = ('equity','signals','orders','fills','cash_ledger','component_ledger','trades')
WINDOWS = [x[0] for x in PROTOCOL.evaluation_windows]


def clean(value):
    if isinstance(value, dict):
        return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [clean(v) for v in value]
    if isinstance(value,(pd.Timestamp,Path)):
        return str(value)
    if isinstance(value,np.generic):
        value = value.item()
    if isinstance(value,float) and not np.isfinite(value):
        return None
    return value


def save_json(path,value):
    atomic_json(path,clean(value))


def periods(leveraged):
    base = [('continuous',PROTOCOL.primary_start,PROTOCOL.primary_end),*PROTOCOL.evaluation_windows]
    for name,start,end in base:
        for scenario in ('base','stress','severe','delay'):
            yield name,str(start),str(end),scenario
    for name,start,end in PROTOCOL.stress_periods:
        if name=='stress_2020' and not leveraged:
            continue
        for scenario in ('base','stress'):
            yield name,str(start),str(end),scenario


class Passive:
    def __init__(self,weights):
        self.weights = weights
    def decide(self,context):
        if not context.initial_session:
            return []
        return [OrderIntent(symbol,SizingMode.ENTER_SLEEVE,target_weight=weight,reason='INITIAL_PASSIVE_ALLOCATION') for symbol,weight in self.weights.items()]


def benchmarks():
    result = {f'BH_{s}':{s:.99} for s in ('QQQM','SMH','SOXX','QQQ','SPY','TQQQ','SPXL','SOXL')}
    result['CASH'] = {}
    for a,b in [('QQQM','SOXX'),('QQQM','SMH'),('TQQQ','SOXL'),('SPXL','SOXL')]:
        result[f'STATIC50_{a}_{b}'] = {a:.495,b:.495}
        if a in ('TQQQ','SPXL'):
            result[f'STATIC33_{a}_{b}'] = {a:.33,b:.33}
    return result


def inventory(path):
    return {str(p.relative_to(path)):file_hash(p) for p in sorted(path.rglob('*')) if p.is_file() and p!=path/'run_complete.json'}


def verify_run(path):
    marker = json.loads((path/'run_complete.json').read_text())
    if marker['files']!=inventory(path):
        raise ValueError(f'V3_SAVED_RUN_HASH_MISMATCH:{path}')
    return marker


def feature_cache(store):
    dates = sorted(set(d for _,d in store.rows))
    symbols = sorted(store.frames)
    return {d:{s:store.rows[(s,d)] for s in symbols if (s,d) in store.rows} for d in dates}


def save_run(result,path,metadata,data):
    from .independent_reconstruction_v3 import compare_saved_equity, reconstruct_daily_equity_v3
    path.mkdir(parents=True,exist_ok=False)
    for name in TABLES:
        frame = getattr(result,name).copy()
        for key,value in metadata.items():
            frame[key] = value
        frame.to_parquet(path/f'{name}.parquet',index=False)
    result.trades.to_csv(path/'trades.csv',index=False)
    first,last = pd.to_datetime(result.equity.date,utc=True).agg(['min','max'])
    scoped_bars = data.bars[data.bars.date.between(first,last)]
    scoped_actions = data.actions[data.actions.ex_date.between(first,last)]
    rebuilt = reconstruct_daily_equity_v3(result.fills,result.cash_ledger,scoped_bars,scoped_actions,candidate_id=metadata['candidate_id'],valuation_dates=result.equity.date,component_ledger=result.component_ledger)
    comparison = compare_saved_equity(rebuilt.daily,result.equity)
    checks = {**rebuilt.summary,**comparison}
    calendar_dates = dict(zip(data.calendar.date,data.calendar.settlement_date,strict=True))
    sale_dates = result.fills[result.fills.side.eq('sell')]
    checks['settlement_calendar_matches'] = all(pd.Timestamp(row.settlement_date)==calendar_dates[pd.Timestamp(row.date)] for row in sale_dates.itertuples())
    checks['passed'] = checks['settlement_calendar_matches'] and comparison['equity_within_tolerance'] and all(checks.get(k,0)==0 for k in ('missing_marks','negative_holding_or_cash_events','dividend_mismatch_count','settlement_mismatch_count','fee_mismatch_count','component_mismatch_count'))
    rebuilt.daily.to_parquet(path/'reconstructed_equity.parquet',index=False)
    for kind in ('dividend_checks','settlement_checks','fee_checks','component_checks'):
        getattr(rebuilt,kind).to_parquet(path/f'{kind}.parquet',index=False)
    save_json(path/'reconciliation.json',checks)
    equity = result.equity.copy()
    if metadata['track_id']=='b':
        equity['approx_3x_invested_exposure'] = equity.invested_exposure*3
    metric = compute_metrics(equity,result.fills,initial_cash=1000,start=metadata['start'],end=metadata['end'],trades=result.trades,cash_ledger=result.cash_ledger)
    from .attribution_v3 import physical_profit_attribution
    attribution = physical_profit_attribution(result.fills,scoped_bars,scoped_actions,start=first,end=last,ending_equity=metric['ending_equity'],dividends=metric['dividend_income'])
    metric.update(attribution)
    peaks = equity.equity.cummax().clip(lower=1000)
    trough = int((1-equity.equity/peaks).to_numpy().argmax())
    recovered = equity.iloc[trough:][equity.equity.iloc[trough:]>=peaks.iloc[trough]]
    metric['drawdown_unrecovered'] = recovered.empty
    metric['drawdown_recovery_date'] = str(recovered.iloc[0]['date']) if len(recovered) else None
    for column in ('cash_settled','cash_unsettled','dividend_receivables'):
        if column in equity:
            metric['average_'+column] = float(equity[column].mean())
            metric['ending_'+column] = float(equity[column].iloc[-1])
    completed = result.trades[result.trades.exit_date.notna()] if 'exit_date' in result.trades else pd.DataFrame()
    if not completed.empty and 'holding_sessions' in completed:
        metric['average_holding_sessions'] = float(completed.holding_sessions.mean())
        metric['median_holding_sessions'] = float(completed.holding_sessions.median())
    metric.update(metadata)
    metric['reconciliation_passed'] = checks['passed'] and attribution['profit_attribution_passed']
    metric['normalized_turnover'] = metric['turnover']/float(equity.equity.mean())
    save_json(path/'metrics.json',metric)
    save_json(path/'run_complete.json',{'metadata':metadata,'account_hash':result.account_hash,'files':inventory(path)})
    return metric


def run_study(manifest_path,acceptance_path,output,*,family='all',pair=None,resume=False):
    manifest_path,acceptance_path,output = map(Path,(manifest_path,acceptance_path,output))
    accepted = verify_acceptance(acceptance_path)
    data = load_market_data(manifest_path)
    binding = {'protocol_hash':PROTOCOL.protocol_hash,'manifest_hash':data.manifest['manifest_hash'],'registry_hash':registry_envelope()['registry_hash'],'source_hashes':source_hashes(),'acceptance_hash':accepted['acceptance_hash'],'notice':NOTICE}
    if output.exists() and any(output.iterdir()):
        if not resume or json.loads((output/'study_binding.json').read_text())!=binding:
            raise ValueError('V3_NONEMPTY_OR_DIFFERENT_OUTPUT')
    else:
        output.mkdir(parents=True,exist_ok=True)
        save_json(output/'study_binding.json',binding)
        save_json(output/'protocol.json',PROTOCOL.as_dict())
        save_json(output/'candidate_registry.json',registry_envelope())
        save_json(output/'data_manifest.json',data.manifest)
        save_json(output/'input_location.json',{'manifest_path':str(manifest_path.resolve())})
        save_json(output/'action_resolutions.json',data.resolutions)
        shutil.copytree(acceptance_path.parent,output/'acceptance')
        for name in binding['source_hashes']:
            saved_source = output/'source'/name
            saved_source.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/name,saved_source)
    candidates = [c for c in candidate_registry() if (family=='all' or c.strategy_id.startswith(family)) and (pair is None or c.universe.pair_id==pair)]
    jobs = [(c.candidate_id,c,None,str(output)) for c in candidates]
    jobs += [(bid,None,weights,str(output)) for bid,weights in benchmarks().items()]
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed
    completed = 0
    with ProcessPoolExecutor(max_workers=4,mp_context=multiprocessing.get_context('spawn'),initializer=_initialize_worker,initargs=(str(manifest_path),)) as pool:
        futures = [pool.submit(_run_candidate,job) for job in jobs]
        for future in as_completed(futures):
            completed += future.result()
    if family=='all' and pair is None:
        from .diagnostics_v3 import run_hypothetical_diagnostics
        run_hypothetical_diagnostics(output)
    return {'output':str(output),'new_runs':completed,'status':'SIMULATIONS_COMPLETE_RUN_RANK_STUDY'}


_WORKER_DATA = None
_WORKER_STORE = None
_WORKER_CACHE = None


def _network_forbidden(*args,**kwargs):
    raise RuntimeError('V3_RESEARCH_WORKER_NETWORK_DISABLED')


def _initialize_worker(manifest_path):
    socket.socket.connect = _network_forbidden
    socket.create_connection = _network_forbidden
    global _WORKER_DATA,_WORKER_STORE,_WORKER_CACHE
    _WORKER_DATA = load_market_data(Path(manifest_path))
    _WORKER_STORE = FeatureStore(_WORKER_DATA.bars)
    _WORKER_CACHE = feature_cache(_WORKER_STORE)


def _run_candidate(job):
    from .strategies_v3 import make_strategy
    identifier,candidate,weights,output = job
    output = Path(output)
    data,store,cache = _WORKER_DATA,_WORKER_STORE,_WORKER_CACHE
    leveraged = candidate.universe.track_id=='b' if candidate else any(s in ('TQQQ','SOXL','SPXL') for s in weights)
    symbols = candidate.universe.tradable_symbols if candidate else tuple(weights) or ('SPY',)
    completed = 0
    for window,start,end,scenario in periods(leveraged or (candidate is None and 'QQQM' not in symbols)):
        target = output/'runs'/identifier/window/scenario
        if (target/'run_complete.json').exists():
            verify_run(target)
            continue
        if target.exists():
            raise ValueError(f'V3_INCOMPLETE_RUN_REQUIRES_INSPECTION:{target}')
        costs = PROTOCOL.costs_track_b if leveraged else PROTOCOL.costs_track_a
        cost = next(c for c in costs if c.name==('stress' if scenario=='delay' else scenario))
        config = EngineConfig(cost_basis_points=cost.basis_points_per_side,execution_delay_sessions=int(scenario=='delay'),include_history=False,feature_cache=cache)
        strategy = make_strategy(candidate,store=store,data=data,config=config,start=start,end=end) if candidate else Passive(weights)
        result = run_engine_v3(data.bars,strategy=strategy,config=config,calendar=data.calendar,tradable_symbols=symbols,start=start,end=end,candidate_id=identifier)
        metadata = {'candidate_id':identifier,'strategy_id':candidate.strategy_id if candidate else 'benchmark','track_id':'b' if leveraged else 'a','pair_id':candidate.universe.pair_id if candidate else identifier,'window_id':window,'cost_scenario':scenario,'start':start,'end':end,'benchmark':candidate is None}
        save_run(result,target,metadata,data)
        shadow = getattr(strategy,'shadow_result',None)
        if shadow is not None:
            save_run(shadow,target/'shadow',{**metadata,'candidate_id':identifier+'__shadow'},data)
            marker = json.loads((target/'run_complete.json').read_text())
            marker['files'] = inventory(target)
            save_json(target/'run_complete.json',marker)
        completed += 1
        print(f'V3 {identifier} {window} {scenario}',flush=True)
    return completed


def rank_study(study):
    study = Path(study)
    verify_acceptance(study/'acceptance/engine_acceptance.json')
    rows, curves = [],{}
    for marker in sorted((study/'runs').glob('*/*/*/run_complete.json')):
        verify_run(marker.parent)
        metric = json.loads((marker.parent/'metrics.json').read_text())
        rows.append(metric)
        frame = pd.read_parquet(marker.parent/'equity.parquet')
        frame['date'] = pd.to_datetime(frame.date,utc=True)
        curves[(metric['candidate_id'],metric['window_id'],metric['cost_scenario'])] = frame
    metrics = pd.DataFrame(rows)
    summaries, evaluation, bootstrap = [],[],{}
    for candidate in candidate_registry():
        cid = candidate.candidate_id
        selected = metrics[metrics.candidate_id.eq(cid)]
        expected = {(w,s) for w,_,_,s in periods(candidate.universe.track_id=='b')}
        present = set(zip(selected.window_id,selected.cost_scenario,strict=True))
        reasons = []
        if not metrics.reconciliation_passed.all():
            reasons.append('STUDY_ACCOUNTING_INTEGRITY_FAILED')
        if present!=expected:
            reasons.append('INCOMPLETE_REQUIRED_RUNS')
        if not selected.empty and not selected.reconciliation_passed.all():
            reasons.append('ACCOUNTING_RECONCILIATION_FAILED')
        ceiling = .5 if candidate.universe.track_id=='b' else .35
        values = {}
        for scenario in ('base','stress','severe','delay'):
            parts = [curves[(cid,w,scenario)] for w in WINDOWS if (cid,w,scenario) in curves]
            if len(parts)!=6:
                continue
            chained = chain_evaluation_windows(pd.concat(parts),WINDOWS)
            evaluation.append(chained)
            values[f'{scenario}_evaluation_return'] = float(chained.evaluation_index.iloc[-1]/1000-1)
            values[f'{scenario}_evaluation_drawdown'] = float(chained.drawdown.max())
            if scenario in ('base','stress','delay'):
                if values[f'{scenario}_evaluation_return']<=0:
                    reasons.append(f'{scenario.upper()}_EVALUATION_NONPOSITIVE')
                if values[f'{scenario}_evaluation_drawdown']>ceiling:
                    reasons.append(f'{scenario.upper()}_EVALUATION_DRAWDOWN')
            if scenario=='base':
                window_metrics = selected[selected.window_id.isin(WINDOWS)&selected.cost_scenario.eq('base')]
                values['base_evaluation_normalized_turnover'] = float(window_metrics.turnover.sum()/pd.concat(parts).equity.mean())
                bootstrap[cid] = {}
                for reference in ('BH_SPY','BH_QQQ'):
                    refparts = [curves.get((reference,w,scenario)) for w in WINDOWS]
                    if all(x is not None for x in refparts):
                        ref = chain_evaluation_windows(pd.concat(refparts),WINDOWS)
                        bootstrap[cid][reference] = pooled_paired_bootstrap(chained,ref)
        basewindows = selected[selected.window_id.isin(WINDOWS)&selected.cost_scenario.eq('base')]
        positive = int((basewindows.net_return>0).sum())
        if positive<4:
            reasons.append('FEWER_THAN_FOUR_POSITIVE_WINDOWS')
        for row in selected.to_dict('records'):
            if row['cost_scenario'] in ('base','stress'):
                if row['max_drawdown']>ceiling:
                    reasons.append(f"{row['window_id']}_{row['cost_scenario']}_DRAWDOWN")
                if row['window_id']=='continuous' and row['net_return']<=0:
                    reasons.append(f"CONTINUOUS_{row['cost_scenario']}_NONPOSITIVE")
            if row['window_id']=='continuous':
                for key in ('net_return','net_pnl','ending_equity','max_drawdown','normalized_turnover'):
                    values[f"{row['cost_scenario']}_continuous_{key}"] = row[key]
        summaries.append({'candidate_id':cid,'group':candidate.universe.pair_id if candidate.universe.track_id=='b' else 'unleveraged','strategy_id':candidate.strategy_id,'pair_id':candidate.universe.pair_id,'qualified':not reasons,'failure_reasons':'; '.join(reasons),'positive_windows':positive,**values})
    leaderboard = pd.DataFrame(summaries)
    leaderboard = leaderboard.sort_values(['qualified','base_evaluation_return','base_evaluation_drawdown','base_evaluation_normalized_turnover','candidate_id'],ascending=[False,False,True,True,True])
    selection = {'notice':NOTICE,'groups':{}}
    for group in ('unleveraged','TQQQ_SOXL','SPXL_SOXL'):
        qualified = leaderboard[leaderboard.group.eq(group)&leaderboard.qualified]
        selection['groups'][group] = {'status':'RESEARCH_SHORTLIST' if len(qualified) else 'NO_QUALIFYING_STRATEGY','candidates':qualified.candidate_id.head(3).tolist()}
    for row in rows:
        curve = curves[(row['candidate_id'],row['window_id'],row['cost_scenario'])]
        for reference in benchmarks():
            reference_curve = curves.get((reference,row['window_id'],row['cost_scenario']))
            if reference_curve is not None:
                if curve.daily_return.std()==0 or reference_curve.daily_return.std()==0:
                    row.update({f'{reference}_return_gap':float((curve.equity.iloc[-1]-reference_curve.equity.iloc[-1])/1000),f'{reference}_beta':0. if reference_curve.daily_return.std()>0 else None,f'{reference}_correlation':None})
                else:
                    row.update(add_benchmark_comparison(curve,reference_curve,prefix=reference))
    pd.DataFrame(rows).to_parquet(study/'metrics.parquet',index=False)
    save_json(study/'metrics.json',{'runs':rows})
    leaderboard.to_csv(study/'leaderboard.csv',index=False)
    pd.concat(evaluation,ignore_index=True).to_parquet(study/'evaluation_daily.parquet',index=False)
    selection['artifact_hashes'] = {name:file_hash(study/name) for name in ('metrics.json','metrics.parquet','leaderboard.csv','evaluation_daily.parquet','candidate_registry.json','protocol.json')}
    save_json(study/'selection.json',selection)
    save_json(study/'bootstrap.json',bootstrap)
    from .diagnostics_v3 import (
        frictionless_references,
        leadership_diagnostics,
        migration_comparison,
    )
    data = load_market_data(Path(json.loads((study/'input_location.json').read_text())['manifest_path']))
    leadership_diagnostics(study,FeatureStore(data.bars))
    migration_comparison(study)
    frictionless_references(study,data)
    return selection


def reproduce_study(study,output,*,resume=False):
    study,output = Path(study),Path(output)
    binding = json.loads((study/'study_binding.json').read_text())
    if binding['source_hashes']!=source_hashes():
        raise ValueError('V3_REPRODUCTION_SOURCE_CHANGED')
    manifest = Path(json.loads((study/'input_location.json').read_text())['manifest_path'])
    def forbidden(*args,**kwargs):
        raise RuntimeError('V3_OFFLINE_NETWORK_DISABLED')
    with patch.object(socket.socket,'connect',forbidden),patch.object(socket,'create_connection',forbidden):
        run_study(manifest,study/'acceptance/engine_acceptance.json',output,resume=resume)
        rank_study(output)
    old = {str(p.relative_to(study)):file_hash(p) for p in (study/'runs').rglob('*') if p.is_file()}
    new = {str(p.relative_to(output)):file_hash(p) for p in (output/'runs').rglob('*') if p.is_file()}
    for name in ('metrics.json','metrics.parquet','leaderboard.csv','evaluation_daily.parquet','selection.json','bootstrap.json'):
        old[name],new[name] = file_hash(study/name),file_hash(output/name)
    evidence = {'passed':old==new,'network':'disabled','compared_artifacts':len(old),'mismatches':sorted(k for k in set(old)|set(new) if old.get(k)!=new.get(k))}
    save_json(output/'reproduction.json',evidence)
    if not evidence['passed']:
        raise ValueError('V3_OFFLINE_REPRODUCTION_MISMATCH')
    return evidence


def archive_study(study):
    from .library import (
        _inventory,
        _load_catalog,
        _save_catalog,
        _verify_inventory,
        require_library,
    )
    study = Path(study)
    library = require_library(Path('/Volumes/T9/TradingResearch'))
    destination = library/'studies/etf-cash-v3'/study.name
    if destination.exists():
        raise ValueError('V3_ARCHIVE_ALREADY_EXISTS')
    for marker in (study/'runs').glob('*/*/*/run_complete.json'):
        verify_run(marker.parent)
    stage = library/'staging'/f'v3-{study.name}'
    shutil.copytree(study,stage)
    files = _inventory(stage)
    save_json(stage/'archive_inventory.json',{'files':files,'inventory_hash':canonical_hash(files)})
    _verify_inventory(stage)
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage.rename(destination)
    catalog = _load_catalog(library)
    catalog['studies'][f'v3-{study.name}'] = {'path':str(destination.relative_to(library)),'snapshot_id':json.loads((study/'data_manifest.json').read_text())['manifest_hash'].removeprefix('sha256:')[:16],'inventory_hash':canonical_hash(files)}
    _save_catalog(library,catalog)
    return {'archive':str(destination)}
