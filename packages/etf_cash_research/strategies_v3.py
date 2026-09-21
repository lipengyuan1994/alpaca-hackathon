"""Frozen strategy-family dispatch for the repaired engine."""
def make_strategy(candidate, *, store, data, config, start, end):
    if candidate.strategy_id.startswith('L'):
        from .strategies_l_v3 import make_l_strategy
        return make_l_strategy(candidate,store=store,data=data,config=config,start=start,end=end)
    from .strategies_sa_v3 import make_sa_strategy
    return make_sa_strategy(candidate,store=store,data=data,config=config,start=start,end=end)
