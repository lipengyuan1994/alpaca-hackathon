"""Persistent, self-contained study report with individual candidate evidence."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

from .reporting import _svg_bars, _svg_line
from .study_v3 import NOTICE


def table(frame):
    frame = frame.copy()
    for col in frame:
        if any(x in col for x in ('return','drawdown','exposure','win_rate','correlation')) and pd.api.types.is_numeric_dtype(frame[col]):
            frame[col] = frame[col].map(lambda x:f'{x:.2%}' if pd.notna(x) else '—')
        elif pd.api.types.is_float_dtype(frame[col]):
            frame[col] = frame[col].map(lambda x:f'{x:,.2f}' if pd.notna(x) else '—')
    return frame.to_html(index=False,escape=True,border=0)


def heatmap(monthly):
    years = sorted({x[:4] for x in monthly})
    rows = ['<table><tr><th>Year</th>'+''.join(f'<th>{x}</th>' for x in range(1,13))+'</tr>']
    for year in years:
        cells = []
        for month in range(1,13):
            value = monthly.get(f'{year}-{month:02d}')
            color = '#fff' if value is None else '#d1fae5' if value>=0 else '#fee2e2'
            label = '—' if value is None else f'{value:.1%}'
            cells.append(f'<td style="background:{color}">{label}</td>')
        rows.append(f'<tr><th>{year}</th>'+''.join(cells)+'</tr>')
    return ''.join(rows)+'</table>'


def scatter(frame):
    width,height,pad = 900,420,65
    maxx = max(.01,float(frame.base_continuous_max_drawdown.max()))
    low = min(0,float(frame.base_continuous_net_return.min()))
    high = max(.01,float(frame.base_continuous_net_return.max()))
    items = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="white"/>']
    for fraction in (0,.25,.5,.75,1):
        x = pad+(width-2*pad)*fraction
        y = height-pad-(height-2*pad)*fraction
        items.append(f'<text x="{x}" y="{height-30}" font-size="12">{fraction*maxx:.0%}</text><text x="0" y="{y}" font-size="12">{low+(high-low)*fraction:.0%}</text>')
    for row in frame.itertuples():
        x = pad+(width-2*pad)*row.base_continuous_max_drawdown/maxx
        y = height-pad-(height-2*pad)*(row.base_continuous_net_return-low)/(high-low)
        color = '#059669' if row.qualified else '#dc2626'
        items.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{color}"><title>{html.escape(row.candidate_id)} return {row.base_continuous_net_return:.2%}, drawdown {row.base_continuous_max_drawdown:.2%}</title></circle>')
    items.append('<text x="350" y="415">Maximum drawdown (%) · continuous account</text></svg>')
    return ''.join(items)


def build_report(study,output):
    study,output = Path(study),Path(output)
    if output.suffix!='.html':
        output = output/'report.html'
    output.parent.mkdir(parents=True,exist_ok=True)
    board = pd.read_csv(study/'leaderboard.csv').fillna({'failure_reasons':''})
    metrics = json.loads((study/'metrics.json').read_text())['runs']
    selection = json.loads((study/'selection.json').read_text())
    from packages.research_data.artifacts import file_hash
    if any(file_hash(study/name)!=digest for name,digest in selection['artifact_hashes'].items()):
        raise ValueError('V3_REPORT_SELECTION_ARTIFACT_CHANGED')
    evaluation = pd.read_parquet(study/'evaluation_daily.parquet')
    sections = [f'<h1>Repaired ETF research · 63 candidates</h1><p class="notice">{NOTICE}</p>',
      '<p><b>Primary dates: September 19, 2023–September 18, 2026.</b> Each account starts with $1,000. Unleveraged costs: 5/15/30 bps per side; leveraged: 10/25/50 bps; $0.01 per external sale. Delayed execution uses stress costs. No capital additions.</p>',
      '<p>Evaluation index: six independent $1,000 accounts joined by daily returns for ranking. It is not an account dollar balance. Continuous P&amp;L comes from a separate uninterrupted account. All periods are reused exploratory history.</p>',
      '<h2>Research shortlists</h2>']
    for group,result in selection['groups'].items():
        sections.append(f'<h3>{html.escape(group)}: {result["status"]}</h3><p>'+html.escape(', '.join(result['candidates']) or 'No candidate passes every gate.')+'</p>')
    display = ['candidate_id','qualified','base_evaluation_return','stress_evaluation_return','base_continuous_net_pnl','base_continuous_net_return','base_continuous_max_drawdown','positive_windows','failure_reasons']
    sections += ['<h2>Every primary candidate</h2><p>Return and drawdown columns are percentages; P&amp;L is USD. Green scatter points pass qualification. Red points fail at least one gate.</p>',table(board[display]),scatter(board)]
    paired = board[board.group.ne('unleveraged')].pivot(index='strategy_id',columns='pair_id',values=['base_continuous_net_return','base_continuous_max_drawdown','base_evaluation_return'])
    paired.columns = [' / '.join(x) for x in paired.columns]
    sections += ['<h2>Paired leveraged results</h2>',table(paired.reset_index())]
    passive = pd.DataFrame([x for x in metrics if x['benchmark'] and x['window_id']=='continuous' and x['cost_scenario']=='base'])
    sections += ['<h2>Passive exposure references — continuous base cost</h2>',table(passive[['candidate_id','ending_equity','net_pnl','net_return','max_drawdown','trading_costs','average_invested_exposure']])]
    sections += ['<h2>Candidate details</h2><p>Click a candidate to inspect charts, stress results, and monthly returns. Ledgers and exact inputs remain in the study directory.</p>']
    charts = output.parent/'charts'
    charts.mkdir(exist_ok=True)
    for row in board.itertuples():
        cid = row.candidate_id
        frame = pd.read_parquet(study/'runs'/cid/'continuous/base/equity.parquet')
        dates = frame.date.astype(str).tolist()
        curves = [(cid,frame)]
        references = ['BH_SPY','BH_QQQ']+[f'BH_{symbol}' for symbol in row.pair_id.split('_')]
        for ref in references:
            curves.append((ref,pd.read_parquet(study/'runs'/ref/'continuous/base/equity.parquet')))
        candidate_metrics = [x for x in metrics if x['candidate_id']==cid]
        detail = [f'<details><summary>{html.escape(cid)} · continuous P&amp;L ${row.base_continuous_net_pnl:,.2f}</summary>']
        for kind,title,column in [('growth','Continuous growth of $1,000','equity'),('profit','Continuous cumulative profit','cumulative_profit'),('drawdown','Continuous drawdown','drawdown')]:
            filename = f'{cid}_{kind}.svg'
            _svg_line(charts/filename,title,[(label,curve[column].tolist()) for label,curve in curves],y_label='drawdown' if kind=='drawdown' else 'USD',dates=dates)
            detail.append(f'<img loading="lazy" src="charts/{filename}" alt="{title}">')
        ev = evaluation[evaluation.candidate_id.eq(cid)&evaluation.cost_scenario.eq('base')].sort_values('date')
        filename = f'{cid}_evaluation.svg'
        _svg_line(charts/filename,'Normalized evaluation index — independent window accounts',[(cid,ev.evaluation_index.tolist())],y_label='index (start 1,000)',dates=ev.date.astype(str).tolist())
        detail.append(f'<img loading="lazy" src="charts/{filename}" alt="evaluation index">')
        base = next(x for x in candidate_metrics if x['window_id']=='continuous' and x['cost_scenario']=='base')
        detail += [heatmap(base['monthly_returns']),table(pd.DataFrame(candidate_metrics)[['window_id','cost_scenario','net_pnl','net_return','max_drawdown','trading_costs','reconciliation_passed']])]
        filename = f'{cid}_costs.svg'
        values = [getattr(row,f'{s}_continuous_net_return') for s in ('base','stress','severe','delay')]
        _svg_bars(charts/filename,'Continuous return by cost and delay scenario',['base','stress','severe','delay'],values,y_label='return')
        detail.append(f'<img loading="lazy" src="charts/{filename}" alt="cost comparison">')
        detail.append('</details>')
        sections.extend(detail)
    for filename,title in [('leadership_diagnostics.csv','Historical leadership subsets'),('hypothetical_stress.csv','Hypothetical path diagnostics — not historical returns or forecasts')]:
        if (study/filename).exists():
            sections.append('<details><summary>'+title+'</summary>'+table(pd.read_csv(study/filename))+'</details>')
    sections += ['<h2>Evidence and interpretation</h2><p>Trades use regular-session opening prices as proxies with adverse costs. Dividends accrue on ex-date to existing holders and become spendable on payable dates. Sale proceeds follow the verified T+2/T+1 settlement calendar. No threshold-price stop fills are assumed.</p>',
      '<p>Bootstrap intervals in bootstrap.json use 2,000 paired moving-block samples, 20 sessions, seed 135, and resample within windows. They are descriptive and do not remove selection bias. Approximately three times invested fraction is an exposure illustration, not measured beta.</p>',
      '<p>Offline reproduction: <code>etf-cash-research reproduce-study --study '+html.escape(str(study.resolve()))+' --output /absolute/new-empty-directory</code>. See study_binding.json for frozen source/input hashes and acceptance/engine_acceptance.json for executable test evidence.</p>']
    body = '<!doctype html><html><head><meta charset="utf-8"><title>ETF research v3</title><style>body{font:15px system-ui;color:#172033;margin:32px auto;max-width:1400px;padding:0 20px}table{border-collapse:collapse;font-size:12px;display:block;overflow:auto}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:right}td:first-child,th:first-child{text-align:left}.notice{background:#fff3cd;padding:15px}details{border:1px solid #ddd;margin:12px 0;padding:14px}summary{cursor:pointer;font-weight:600}img,svg{width:100%;max-width:1000px}h2{margin-top:40px}</style></head><body>'+''.join(sections)+'</body></html>'
    output.write_text(body)
    (output.parent/'report.md').write_text('# ETF research v3\n\n'+NOTICE+'\n\nPrimary dates: 2023-09-19 through 2026-09-18. See report.html for full charts and leaderboard.csv for every candidate.\n\n'+json.dumps(selection,indent=2)+'\n')
    return str(output)
