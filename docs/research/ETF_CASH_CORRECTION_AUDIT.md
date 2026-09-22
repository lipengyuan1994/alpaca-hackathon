# ETF v2 correction audit — 2026-09-20

The original `20260920_audited_all` release overstates completion. Preserve it as
historical evidence, but use `output/etf_cash_research_v2/20260920_corrected_calculations/report.html`
for the corrected arithmetic and migration comparison. Neither release certifies
execution conformance or live readiness.

## Resolved calculation defects

- Chain all six independent $1,000 window accounts using their daily returns,
  including the first marked day's return from initial capital. The normalized
  index carries forward across account resets.
- Calculate index drawdown against its running peak, including starting capital.
  Apply the same calculation to the delayed-execution index. Keep individual
  window drawdown gates separately.
- Include first-day losses in continuous account risk, benchmark comparisons and
  monthly heatmaps.
- Calculate win rate over all completed trades, not just winning trades. Use
  root-mean-square downside returns over all observations for zero-MAR Sortino.
- Preserve paired observations in the moving-block bootstrap. Publish 2.5th and
  97.5th percentile endpoints; pooled-index resampling stays within each window.
- Read stress and severe results for the cost chart instead of filling them with
  zero. Add dated line-chart axes and percentage return labels.

The corrected saved execution paths have **21 numerical performance-gate passes**,
down from 22. L08/TQQQ+SOXL fails the corrected delayed-index drawdown gate at
**51.2609%**, above the 50% ceiling. The numerical top-three groups do not change.
These are diagnostic performance gates, not complete strategy qualification.

## Resolved S01/S10 migration discrepancy

QQQM starts on 2020-10-13 while SMH starts on 2019-01-02. Passing one positional
index to both independently indexed histories shifted SMH by 449 sessions.
The simulator now aligns every symbol to a common calendar before evaluating
positional strategies. It rejects gaps inside the common coverage instead of
silently dropping a missing session.

At the first S01 signal divergence, information cutoff 2023-10-02 was incorrectly
paired with SMH data from 2021-12-16. The wrong decision selected SMH; the aligned
decision selected QQQM. The controlled ablation reproduces the old discrepancy.

Continuous base costs, 2023-09-19 through 2026-09-18:

| Reference | Erroneous v2 return | Corrected return | Corrected ending equity | Maximum drawdown |
|---|---:|---:|---:|---:|
| S01 QQQM+SMH | 43.7242% | 138.8549% | $2,388.55 | 24.4946% |
| S10 QQQM+SMH | 140.4482% | 164.7869% | $2,647.87 | 19.0240% |

All six strategy/cost comparisons match legacy daily equity and fills exactly;
there is no residual raw-versus-split-price discrepancy in this primary period.
The old frozen selection history has not been rewritten.

## Independent evidence

An independent reconstruction of all 30 primary continuous base accounts uses
fills, raw closing prices, corporate actions and cash-ledger events. It does not
use saved daily-profit sums. There are zero equity or dividend-entitlement
mismatches; the maximum absolute daily equity difference is below $0.000000001.
This establishes ledger reconciliation for those accounts, not signal/execution
rule compliance.

The corrected directory contains `metric_corrections.csv`, `evaluation_index.parquet`,
`pooled_bootstrap.json`, `migration_comparison.json`, `reconstruction_audit.json`,
`numerical_shortlist.csv`, `correction_audit.json`, and failing
`execution_conformance_probes.json` evidence.

## Execution conformance remains incomplete

This correction does **not** fix the following separately discovered engine gaps:

1. Target quantities use current-session opening prices instead of prior-close
   prices. A $500 intent with a $100 prior close and $200 open requests 2.5 shares
   rather than the specified 5 shares.
2. Reviews use signal-session boundaries; unchanged nominal targets can skip a
   scheduled rebalance.
3. Holding ages and cooldowns are not driven by actual fill feedback.
4. Delayed execution carries weights rather than the frozen original quantities.
5. A09's shadow account and ensemble component ledgers do not implement the
   required independent cash and virtual-position accounting.

`rank_study` blocks qualification when these findings appear in
`correction_audit.json`. The report exposes numerical performance-gate results
separately. Repairing those execution paths requires fresh simulations; corrected
statistics over saved fills cannot stand in for that work.

## Offline reproduction of the arithmetic correction

Use verified native ARM64 Python. The command validates the exact saved manifest,
rejects an existing destination and makes no network calls:

```sh
/opt/homebrew/bin/uv run --frozen python -m packages.etf_cash_research.corrections_v2 \
  --source output/etf_cash_research_v2/20260920_audited_all \
  --output output/etf_cash_research_v2/my_new_arithmetic_reproduction
```

This command reproduces derived statistics and paired intervals from saved
executions. Migration ablations use `_write_migration_comparison` in `study_v2.py`;
independent reconstruction uses `python -m packages.etf_cash_research.reconstruction_v2`.
It is not a substitute for reproducing a corrected execution engine.

Deterministic research backtest; historical periods reused; LLM overlay not
performance-tested; no live authorization.
