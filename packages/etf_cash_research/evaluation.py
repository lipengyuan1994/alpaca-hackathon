"""Chronological chaining of independently funded evaluation accounts."""

from __future__ import annotations

import numpy as np
import pandas as pd


def chain_evaluation_windows(
    frame: pd.DataFrame, window_ids: list[str] | tuple[str, ...], *, initial_cash: float = 1000.0,
) -> pd.DataFrame:
    """Include every window's first-day P&L and carry the index across resets.

    Input must describe exactly one candidate, phase and cost scenario. Missing
    windows, duplicate dates and invalid marks are errors, never zero returns.
    """
    if initial_cash <= 0 or not window_ids or len(set(window_ids)) != len(window_ids):
        raise ValueError("ETF_EVALUATION_INVALID_WINDOW_SPEC")
    required = {"date", "window_id", "equity"}
    if not required.issubset(frame.columns):
        raise ValueError("ETF_EVALUATION_MISSING_COLUMNS")
    selected = frame[frame["window_id"].isin(window_ids)].copy()
    if set(selected["window_id"]) != set(window_ids):
        raise ValueError("ETF_EVALUATION_WINDOW_COVERAGE_INCOMPLETE")
    for key in ("candidate_id", "phase", "cost_scenario"):
        if key in selected and selected[key].nunique(dropna=False) != 1:
            raise ValueError("ETF_EVALUATION_MIXED_ACCOUNTS")
    selected["date"] = pd.to_datetime(selected["date"], utc=True)
    if selected["date"].isna().any() or selected["date"].duplicated().any():
        raise ValueError("ETF_EVALUATION_INVALID_DATES")
    parts = []
    prior_end = None
    for window_id in window_ids:
        part = selected[selected["window_id"] == window_id].sort_values("date").copy()
        values = pd.to_numeric(part["equity"], errors="coerce")
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError("ETF_EVALUATION_INVALID_EQUITY")
        if prior_end is not None and part["date"].iloc[0] <= prior_end:
            raise ValueError("ETF_EVALUATION_NONCHRONOLOGICAL_WINDOWS")
        prior_end = part["date"].iloc[-1]
        part["daily_return"] = values.div(values.shift(1).fillna(initial_cash)).sub(1.0)
        parts.append(part)
    result = pd.concat(parts, ignore_index=True)
    result["evaluation_index"] = initial_cash * (1.0 + result["daily_return"]).cumprod()
    peaks = result["evaluation_index"].cummax().clip(lower=initial_cash)
    result["drawdown"] = 1.0 - result["evaluation_index"] / peaks
    return result


def pooled_paired_bootstrap(strategy: pd.DataFrame, benchmark: pd.DataFrame, *, samples: int = 2000, block_length: int = 20, seed: int = 135) -> dict:
    """Paired moving blocks sampled within each independent account window."""
    paired = strategy[["date", "window_id", "daily_return"]].merge(
        benchmark[["date", "window_id", "daily_return"]], on=["date", "window_id"],
        suffixes=("_strategy", "_benchmark"), validate="one_to_one",
    )
    if len(paired) != len(strategy) or len(paired) != len(benchmark):
        raise ValueError("ETF_BOOTSTRAP_PAIRED_COVERAGE_MISMATCH")
    rng = np.random.default_rng(seed)
    strategy_growth, benchmark_growth = np.ones(samples), np.ones(samples)
    for _, group in paired.groupby("window_id", sort=True):
        group = group.sort_values("date")
        count = len(group)
        block = min(block_length, count)
        starts = rng.integers(0, count - block + 1, size=(samples, int(np.ceil(count / block))))
        indices = (starts[:, :, None] + np.arange(block)).reshape(samples, -1)[:, :count]
        strategy_growth *= np.prod(1 + group.daily_return_strategy.to_numpy()[indices], axis=1)
        benchmark_growth *= np.prod(1 + group.daily_return_benchmark.to_numpy()[indices], axis=1)
    gaps = strategy_growth - benchmark_growth
    return {"samples": samples, "block_length": block_length, "seed": seed, "confidence_level": .95,
            "resampling": "paired_within_each_independent_window_then_chained",
            "return_gap_quantiles": dict(zip(["p025", "p50", "p975"], map(float, np.quantile(gaps, [.025, .5, .975])), strict=True)),
            "interpretation": "descriptive_interval; does_not_remove_strategy_selection_bias"}
