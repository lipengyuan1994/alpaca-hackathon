"""Physical-account FIFO profit attribution, independent of virtual transfers."""
from __future__ import annotations

from collections import defaultdict

import pandas as pd


def physical_profit_attribution(fills,bars,actions,*,start,end,ending_equity,dividends):
    start,end = pd.Timestamp(start),pd.Timestamp(end)
    start = start.tz_localize('UTC') if start.tzinfo is None else start
    end = end.tz_localize('UTC') if end.tzinfo is None else end
    holdings = defaultdict(list)
    events = defaultdict(list)
    for row in fills.to_dict('records'):
        events[pd.Timestamp(row['date'])].append(('fill',row))
    for row in actions.to_dict('records'):
        when = pd.Timestamp(row['ex_date'])
        if start<=when<=end and 'split' in row['action_type']:
            events[when].insert(0,('split',row))
    realized = 0.
    for _,items in sorted(events.items()):
        for kind,row in items:
            symbol = row['symbol']
            if kind=='split':
                factor = float(row['new_rate'])/float(row['old_rate'])
                for lot in holdings[symbol]:
                    lot[0] = round(lot[0]*factor)
                    lot[1] /= factor
                continue
            quantity = round(float(row['quantity'])*1e6)
            price = float(row['price'])
            if row['side']=='buy':
                holdings[symbol].append([quantity,price])
            else:
                remaining = quantity
                basis = 0.
                while remaining:
                    if not holdings[symbol]:
                        raise ValueError('V3_ATTRIBUTION_NEGATIVE_HOLDINGS')
                    lot = holdings[symbol][0]
                    used = min(remaining,lot[0])
                    basis += used/1e6*lot[1]
                    lot[0] -= used
                    remaining -= used
                    if not lot[0]:
                        holdings[symbol].pop(0)
                realized += quantity/1e6*price-basis-float(row['fee'])
    marks = bars[bars.date<=end].sort_values('date').groupby('symbol').tail(1).set_index('symbol').close.to_dict()
    unrealized = sum(q/1e6*(float(marks[s])-p) for s,lots in holdings.items() for q,p in lots)
    error = float(ending_equity)-1000-(realized+unrealized+float(dividends))
    return {'realized_pnl':realized,'unrealized_pnl':unrealized,'profit_attribution_error':error,'profit_attribution_passed':abs(error)<=.01,'profit_attribution_definition':'physical_FIFO; external execution costs embedded; dividends separate; internal transfers excluded'}
