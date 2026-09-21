# ETF cash research v2

This package adds the QQQM+SMH Track A and leveraged Track B study. It is a
deterministic historical research suite. Historical periods are reused, the
Gemini news overlay is not performance-tested, and no command in this package
has live account or order authority.

The original v1 QQQM/SOXX/SMH study and its selection artifacts remain
unchanged. v2 uses `protocol_v2.py`, `simulator_v2.py`, `strategies_track_a.py`
and `strategies_track_b.py` with independent output directories.

## Data collection

Use the native ARM64 environment and the extended collection configuration:

```text
/opt/homebrew/bin/uv run --frozen etf-cash-research collect-v2 \
  --spec configs/etf_cash_collection_v2_extended.yaml \
  --output /private/tmp/etf-cash-collection-v2-extended
```

The collector records raw and split-consistent bars, corporate actions, feed
probes and hashes. The extended configuration starts in 2019 so the 2020 and
2022 stress checks have the required indicator history. A missing corporate
action ratio or dividend payable date remains a qualification failure.

## Study commands

```text
/opt/homebrew/bin/uv run --frozen etf-cash-research audit-engine

/opt/homebrew/bin/uv run --frozen etf-cash-research run-study \
  --data-manifest /path/to/data_manifest.json \
  --track all \
  --protocol configs/etf_cash_research_v2.yaml \
  --output /path/to/etf_cash_v2_study

# Pair-parallel Track B runs are equivalent to --track b but reduce wall time:
/opt/homebrew/bin/uv run --frozen etf-cash-research run-study \
  --data-manifest /path/to/data_manifest.json --track b --pair TQQQ_SOXL \
  --protocol configs/etf_cash_research_v2.yaml --output /path/to/tqqq_soxl
/opt/homebrew/bin/uv run --frozen etf-cash-research run-study \
  --data-manifest /path/to/data_manifest.json --track b --pair SPXL_SOXL \
  --protocol configs/etf_cash_research_v2.yaml --output /path/to/spxl_soxl

/opt/homebrew/bin/uv run --frozen etf-cash-research rank-study \
  --study /path/to/etf_cash_v2_study \
  --protocol configs/etf_cash_research_v2.yaml

/opt/homebrew/bin/uv run --frozen etf-cash-research report-v2 \
  --study /path/to/etf_cash_v2_study \
  --output /path/to/etf_cash_v2_study/report.html

/opt/homebrew/bin/uv run --frozen etf-cash-research reproduce-study \
  --study /path/to/etf_cash_v2_study \
  --output /path/to/reproduced_v2

/opt/homebrew/bin/uv run --frozen etf-cash-research merge-study \
  --study /path/to/track_a --study /path/to/tqqq_soxl --study /path/to/spxl_soxl \
  --output /path/to/etf_cash_v2_all
```

`--track a` runs the ten A candidates on QQQM+SMH. `--track b` runs the ten L
candidates on both TQQQ+SOXL and SPXL+SOXL. Track B uses its 10/25/50 basis-point
cost scenarios and a 50% drawdown ceiling; Track A uses 5/15/30 basis points and
a 35% ceiling.

Each candidate has continuous, six-window evaluation and one-session-delay
artifacts. `normalized/equity_daily.parquet`, `signals`, `orders`, `fills`,
`cash_ledger` and `trades` are aggregate immutable ledgers. Candidate metrics
are under `<candidate>/<cost>/<phase>/metrics.json`; benchmarks are under
`benchmarks/`. `selection.json` is written only by `rank-study` after the
qualification gates pass.

Add `--sensitivities` to `run-study` for the two registered diagnostic variants
per strategy. Sensitivity candidates are recorded separately and never replace
primary candidates in ranking.

For the complete walk-forward execution-delay gate, add `--delay-windows`.
This preserves the full-period delay diagnostic and also writes one-session
delayed results for W1--W6. The persistent release is stored under
`output/etf_cash_research_v2/20260920_audited_all/`, with a stable
`output/etf_cash_research_v2/index.html` entry point.

The six evaluation windows reset the account at $1,000 and are chained only as
a normalized return index for ranking. The continuous account remains a
separate uninterrupted $1,000 simulation. This prevents a chart’s full-period
winner from silently replacing the frozen walk-forward selection rule.

## Verification

```text
/opt/homebrew/bin/uv run --frozen pytest -q \
  tests/etf_cash_research/test_v2_engine.py \
  tests/etf_cash_research/test_track_a.py \
  tests/etf_cash_research/test_track_b.py
/opt/homebrew/bin/uv run --frozen ruff check packages/etf_cash_research
```
