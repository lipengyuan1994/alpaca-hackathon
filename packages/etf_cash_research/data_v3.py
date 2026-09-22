"""Verified raw inputs and causal, forward-split-consistent feature cache."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from packages.research_data.artifacts import file_hash

from .collector import load_manifest


def wilder(values: pd.Series, period: int) -> pd.Series:
    result = pd.Series(np.nan, index=values.index, dtype=float)
    valid = values.dropna()
    if len(valid) < period:
        return result
    first = values.index.get_loc(valid.index[period - 1])
    result.iloc[first] = valid.iloc[:period].mean()
    for i in range(first + 1, len(values)):
        result.iloc[i] = (result.iloc[i - 1] * (period - 1) + values.iloc[i]) / period
    return result


def exponential(values: pd.Series, period: int) -> pd.Series:
    result = pd.Series(np.nan, index=values.index, dtype=float)
    if len(values) >= period:
        result.iloc[period - 1] = values.iloc[:period].mean()
        alpha = 2 / (period + 1)
        for i in range(period, len(values)):
            result.iloc[i] = alpha * values.iloc[i] + (1 - alpha) * result.iloc[i - 1]
    return result


@dataclass
class MarketData:
    bars: pd.DataFrame
    actions: pd.DataFrame
    calendar: pd.DataFrame
    manifest: dict
    manifest_path: Path
    resolutions: dict


def load_market_data(manifest_path: Path) -> MarketData:
    if str(manifest_path.absolute()).startswith('/Volumes/T9/'):
        from .library import require_library
        require_library(Path('/Volumes/T9/TradingResearch'))
    manifest = load_manifest(manifest_path)
    datasets = {}
    for item in manifest['datasets']:
        for artifact in [item['artifact'], *item.get('raw_pages', [])]:
            path = manifest_path.parent / artifact['path']
            if not path.is_file() or file_hash(path) != artifact['sha256']:
                raise ValueError(f"V3_INPUT_HASH_MISMATCH:{artifact['path']}")
        datasets[item['dataset_id']] = pd.read_parquet(manifest_path.parent / item['artifact']['path'])
        if len(datasets[item['dataset_id']]) != item['artifact']['rows']:
            raise ValueError('V3_INPUT_ROW_COUNT_MISMATCH')
    raw_metadata = next(x for x in manifest['datasets'] if x['dataset_id'] == 'stock_bars_raw')
    if raw_metadata.get('feed') != ['sip'] or raw_metadata.get('adjustment') != 'raw':
        raise ValueError('V3_REQUIRES_UNIFORM_RAW_SIP')
    bars = datasets['stock_bars_raw'].rename(columns={'event_time': 'date'}).copy()
    bars['date'] = pd.to_datetime(bars.date, utc=True).dt.normalize()
    bars = bars.sort_values(['symbol', 'date']).reset_index(drop=True)
    if bars.duplicated(['symbol', 'date']).any():
        raise ValueError('V3_DUPLICATE_BAR')
    if bars[['open','high','low','close']].isna().any().any() or (bars[['open','high','low','close']] <= 0).any().any():
        raise ValueError('V3_INVALID_PRICE')
    actions = datasets['corporate_actions'].copy()
    actions['ex_date'] = pd.to_datetime(actions.ex_date, utc=True).dt.normalize()
    actions['payable_date'] = pd.to_datetime(actions.payable_date, utc=True, errors='coerce').dt.normalize()
    resolutions = json.loads((Path(__file__).resolve().parents[2]/'configs/etf_cash_action_resolutions_v3.json').read_text())
    if manifest['manifest_hash'] == resolutions['input_manifest_hash']:
        for fix in resolutions['resolutions']:
            keep = actions[actions.id.eq(fix['retain_id'])]
            reject = actions[actions.id.eq(fix['exclude_id'])]
            if len(keep)!=1 or len(reject)!=1 or float(keep.iloc[0]['rate'])!=fix['rate'] or str(keep.iloc[0]['payable_date'].date())!=fix['verified_payable_date']:
                raise ValueError('V3_ACTION_RESOLUTION_MISMATCH')
            actions = actions[~actions.id.eq(fix['exclude_id'])].copy()
    else:
        resolutions = {'resolutions':[]}
    bars['split_factor'] = 1.0
    bars['dividend'] = 0.0
    bars['dividend_payable_date'] = pd.Series(pd.NaT,index=bars.index,dtype='datetime64[ns, UTC]')
    for row in actions.to_dict('records'):
        mask = bars.symbol.eq(row['symbol']) & bars.date.eq(row['ex_date'])
        if 'split' in row['action_type']:
            old, new = float(row['old_rate']), float(row['new_rate'])
            if not np.isfinite([old,new]).all() or min(old,new) <= 0:
                raise ValueError('V3_SPLIT_OFFICIAL_RATIO_MISSING')
            bars.loc[mask,'split_factor'] *= new / old
        elif 'dividend' in row['action_type']:
            rate = float(row['rate'])
            if not np.isfinite(rate) or rate < 0:
                raise ValueError('V3_DIVIDEND_RATE_INVALID')
            if (bars.loc[mask,'dividend'] > 0).any():
                raise ValueError('V3_MULTIPLE_DISTRIBUTIONS_REQUIRE_SEPARATE_EVENTS')
            bars.loc[mask,'dividend'] = rate
            bars.loc[mask,'dividend_payable_date'] = row['payable_date']
    calendar = datasets['calendar'].copy()
    for col in ['date','settlement_date']:
        calendar[col] = pd.to_datetime(calendar[col],utc=True).dt.normalize()
    calendar = calendar.sort_values('date').reset_index(drop=True)
    if calendar.date.duplicated().any() or calendar.settlement_date.isna().any():
        raise ValueError('V3_INVALID_CALENDAR')
    expected = set(calendar.date)
    for symbol, rows in bars.groupby('symbol'):
        required = {d for d in expected if rows.date.min() <= d <= rows.date.max()}
        if set(rows.date) != required:
            raise ValueError(f'V3_SESSION_GAP:{symbol}')
    return MarketData(bars, actions, calendar, manifest, manifest_path.resolve(),resolutions)


class FeatureStore:
    """Prices are forward-adjusted using splits known on each row only.

    This makes ATR/high-water references stable through later splits. No future
    split can change an earlier feature. Ratios and returns are unit invariant.
    """
    def __init__(self, bars: pd.DataFrame):
        self.frames: dict[str,pd.DataFrame] = {}
        self.rows: dict[tuple[str,pd.Timestamp],dict] = {}
        self.cross: dict[tuple[str,str],pd.DataFrame] = {}
        for symbol, raw in bars.groupby('symbol',sort=True):
            f = raw.sort_values('date').set_index('date').copy()
            factor = f.get('split_factor',pd.Series(1.,index=f.index)).cumprod()
            for col in ['open','high','low','close']:
                f[col] *= factor
            c = f.close
            f['r1'] = c.pct_change(fill_method=None)
            for n in [5,21,63,126]:
                f[f'r{n}'] = c.pct_change(n,fill_method=None)
            for n in [20,50,100,200]:
                f[f'sma{n}'] = c.rolling(n,min_periods=n).mean()
            f['sma50_lag20'] = f.sma50.shift(20)
            for n in [20,100]:
                f[f'ema{n}'] = exponential(c,n)
            for n in [20,55]:
                f[f'hh{n}'] = f.high.shift(1).rolling(n).max()
            f['ll20'] = f.low.shift(1).rolling(20).min()
            f['prev_high'] = f.high.shift(1)
            f['std20'] = c.rolling(20).std(ddof=1)
            tr = pd.concat([f.high-f.low,(f.high-c.shift()).abs(),(f.low-c.shift()).abs()],axis=1).max(axis=1)
            f['atr14'] = wilder(tr,14)
            change = c.diff()
            gain, loss = wilder(change.clip(lower=0),2), wilder(-change.clip(upper=0),2)
            f['rsi2'] = 100 - 100/(1+gain/loss.replace(0,np.nan))
            f.loc[(loss == 0)&(gain > 0),'rsi2'] = 100
            f.loc[(loss == 0)&(gain == 0),'rsi2'] = 50
            for n in [60,63]:
                f[f'vol{n}'] = f.r1.rolling(n).std(ddof=1)*np.sqrt(252)
            f['bandwidth'] = 4*f.std20/f.sma20
            f['bandwidth_p20'] = f.bandwidth.shift().rolling(126).quantile(.2)
            f['contraction'] = f.bandwidth < f.bandwidth_p20
            f['er63'] = (c-c.shift(63)).abs()/c.diff().abs().rolling(63).sum().replace(0,np.nan)
            f['trend_persistence20'] = (c>f.sma100).astype(float).where(f.sma100.notna()).rolling(20).mean()
            f['above101_3'] = (c>1.01*f.sma200).rolling(3).sum()==3
            f['below099_2'] = (c<.99*f.sma200).rolling(2).sum()==2
            f['recovery5'] = ((c>f.ema20)&(f.r5>-.06)).rolling(5).sum()==5
            f['session_index'] = np.arange(len(f))
            self.frames[symbol] = f
            self.rows.update({(symbol,d):row for d,row in zip(f.index,f.to_dict('records'),strict=True)})

    def at(self,symbol: str,cutoff) -> dict:
        return self.rows.get((symbol,pd.Timestamp(cutoff)),{})

    def pair(self, broad: str, sector: str) -> pd.DataFrame:
        key = (broad,sector)
        if key not in self.cross:
            a,b = self.frames[broad],self.frames[sector]
            f = pd.DataFrame({'a':a.r1,'b':b.r1}).dropna()
            f['cov60'] = f.a.rolling(60).cov(f.b)*252
            f['var_a60'] = f.a.rolling(60).var()*252
            f['var_b60'] = f.b.rolling(60).var()*252
            f['corr60'] = f.a.rolling(60).corr(f.b)
            ratio = b.close/a.close
            f['ratio'] = ratio
            f['ratio_sma20'] = ratio.rolling(20).mean()
            self.cross[key] = f
        return self.cross[key]
